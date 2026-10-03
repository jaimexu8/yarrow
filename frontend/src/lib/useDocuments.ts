'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { isWorkingOn, listDocuments, type DocumentSummary } from './documents';
import { toApiError } from './errors';

const POLL_INTERVAL_MS = 4000;
const RETRY_INTERVAL_MS = 8000;

/**
 * The signed-in user's documents, newest first.
 *
 * Bump refreshKey to reload, e.g. after an upload finishes. While any
 * document is still queued or processing, the list also re-checks every few
 * seconds, so finished and failed documents update without a page reload. A
 * failed load keeps whatever list is already on screen and retries shortly,
 * so a brief network or server hiccup recovers on its own.
 */
export function useDocuments(refreshKey = 0) {
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  // Deleted this session. A poll that was already in flight when a document
  // was deleted must not bring its row back.
  const removedIds = useRef(new Set<string>());

  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function load() {
      try {
        const docs = (await listDocuments()).filter(
          (doc) => !removedIds.current.has(doc.id)
        );
        if (!current) return;
        setDocuments(docs);
        setError(null);
      } catch (err) {
        if (!current) return;
        setError(toApiError(err).message);
        timer = setTimeout(load, RETRY_INTERVAL_MS);
      }
    }

    load();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [refreshKey, reloads]);

  // Poll while anything is queued or processing
  useEffect(() => {
    // If no documents are currently being worked on, don't set a poll timer
    if (!documents?.some(isWorkingOn)) return;

    const timer = setTimeout(() => setReloads((count) => count + 1), POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [documents]);

  // Swap in a document the server just returned
  const replace = useCallback((changed: DocumentSummary) => {
    setDocuments((current) =>
      (current ?? []).map((doc) => (doc.id === changed.id ? changed : doc))
    );
  }, []);

  /** Drop a document that was just deleted. */
  const remove = useCallback((id: string) => {
    removedIds.current.add(id);
    setDocuments((current) => (current ?? []).filter((doc) => doc.id !== id));
  }, []);

  // Callback to fetch the list again
  const reload = useCallback(() => setReloads((count) => count + 1), []);

  return { documents, error, replace, remove, reload };
}
