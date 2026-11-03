'use client';

import { useEffect, useState, useSyncExternalStore } from 'react';
import { isAxiosError } from 'axios';
import {
  getBatchProgress,
  isBatchWorking,
  type BatchProgress,
} from './documents';
import { toApiError } from './errors';

const POLL_INTERVAL_MS = 4000;
const RETRY_INTERVAL_MS = 8000;

/** Where the Upload page remembers the run it last started (US-31). */
export const BATCH_STORAGE_KEY = 'yarrow.upload.batchId';

const listeners = new Set<() => void>();
// The id for this page load. Storage can be unavailable (private mode,
// blocked site data); the run then still shows while the page is open.
let inMemoryBatchId: string | null = null;

/**
 * The batch id of the last upload run, kept for this browser tab so that
 * leaving the Upload page and coming back shows the run's latest progress.
 */
export function readStoredBatchId(): string | null {
  try {
    return sessionStorage.getItem(BATCH_STORAGE_KEY) ?? inMemoryBatchId;
  } catch {
    return inMemoryBatchId;
  }
}

export function storeBatchId(batchId: string | null): void {
  inMemoryBatchId = batchId;
  try {
    if (batchId) sessionStorage.setItem(BATCH_STORAGE_KEY, batchId);
    else sessionStorage.removeItem(BATCH_STORAGE_KEY);
  } catch {
    // Kept in memory only.
  }
  listeners.forEach((notify) => notify());
}

function subscribe(notify: () => void): () => void {
  listeners.add(notify);
  return () => {
    listeners.delete(notify);
  };
}

function serverSnapshot(): string | null {
  // The server has no sessionStorage; the client re-reads after hydration.
  return null;
}

/** The remembered batch id, updated whenever ``storeBatchId`` is called. */
export function useStoredBatchId(): string | null {
  return useSyncExternalStore(subscribe, readStoredBatchId, serverSnapshot);
}

type Loaded = {
  /** Which batch these results are for, so a new id starts from nothing. */
  forId: string | null;
  progress: BatchProgress | null;
  error: string | null;
  gone: boolean;
};

const NOTHING: Loaded = {
  forId: null,
  progress: null,
  error: null,
  gone: false,
};

/**
 * Progress of one upload run (US-31), re-read every few seconds while any
 * of its files is still queued or processing, so counts move as the worker
 * gets through them. A failed read keeps the last counts and retries
 * shortly. Bump ``refreshKey`` to re-read at once, e.g. after another file
 * of the run has been uploaded.
 *
 * ``gone`` is set when the server no longer knows the batch (it answers 404
 * for an unknown id or someone else's), so the page can forget it.
 */
export function useBatchProgress(batchId: string | null, refreshKey = 0) {
  const [loaded, setLoaded] = useState<Loaded>(NOTHING);
  const [reloads, setReloads] = useState(0);
  // Results for a previous batch id are stale; show a clean slate instead.
  const { progress, error, gone } = loaded.forId === batchId ? loaded : NOTHING;

  useEffect(() => {
    if (!batchId) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function load() {
      try {
        const latest = await getBatchProgress(batchId as string);
        if (!current) return;
        setLoaded({
          forId: batchId,
          progress: latest,
          error: null,
          gone: false,
        });
      } catch (err) {
        if (!current) return;
        if (isAxiosError(err) && err.response?.status === 404) {
          setLoaded({
            forId: batchId,
            progress: null,
            error: null,
            gone: true,
          });
          return;
        }
        const message = toApiError(err).message;
        setLoaded((previous) => ({
          forId: batchId,
          // Keep the last counts of this batch on screen while retrying.
          progress: previous.forId === batchId ? previous.progress : null,
          error: message,
          gone: false,
        }));
        timer = setTimeout(load, RETRY_INTERVAL_MS);
      }
    }

    load();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [batchId, refreshKey, reloads]);

  // Poll while anything in the batch is still being worked on.
  useEffect(() => {
    if (!progress || !isBatchWorking(progress)) return;
    const timer = setTimeout(
      () => setReloads((count) => count + 1),
      POLL_INTERVAL_MS
    );
    return () => clearTimeout(timer);
  }, [progress]);

  return { progress, error, gone };
}
