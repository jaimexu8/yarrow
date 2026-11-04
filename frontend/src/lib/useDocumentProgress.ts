'use client';

import { useEffect, useState } from 'react';
import {
  getDocumentProgress,
  isJobFinished,
  PROGRESS_POLL_INTERVAL_MS,
  type DocumentProgress,
} from './progress';

type Loaded = { forId: string; progress: DocumentProgress };

function isVisible(): boolean {
  return document.visibilityState === 'visible';
}

export function useDocumentProgress(
  documentId: string,
  enabled: boolean
): DocumentProgress | null {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [wasEnabled, setWasEnabled] = useState(enabled);
  if (enabled !== wasEnabled) {
    setWasEnabled(enabled);
    if (enabled) setLoaded(null);
  }

  useEffect(() => {
    if (!enabled) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;

    function schedule() {
      clearTimeout(timer);
      if (current && isVisible()) {
        timer = setTimeout(poll, PROGRESS_POLL_INTERVAL_MS);
      }
    }

    async function poll() {
      if (!current || inFlight || !isVisible()) return;
      inFlight = true;
      try {
        const progress = await getDocumentProgress(documentId);
        if (!current) return;
        setLoaded({ forId: documentId, progress });
        if (isJobFinished(progress)) {
          current = false;
          return;
        }
      } catch {
        if (!current) return;
      } finally {
        inFlight = false;
      }
      schedule();
    }

    function onVisibilityChange() {
      if (isVisible()) poll();
      else clearTimeout(timer);
    }

    poll();
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      current = false;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [documentId, enabled]);

  return loaded?.forId === documentId ? loaded.progress : null;
}
