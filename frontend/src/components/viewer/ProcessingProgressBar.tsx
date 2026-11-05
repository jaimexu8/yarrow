'use client';

import { useEffect, useRef, useState } from 'react';
import { isWorkingOn } from '@/lib/documents';
import { cn } from '@/lib/cn';
import { isJobFinished, stageLabel } from '@/lib/progress';
import { useDocumentProgress } from '@/lib/useDocumentProgress';
import { useViewer } from './ViewerContext';

export const PROGRESS_FADE_MS = 300;

export function ProcessingProgressBar() {
  const { document, refresh } = useViewer();
  const working = isWorkingOn(document);
  const progress = useDocumentProgress(document.id, working);
  const finished = isJobFinished(progress);
  const refreshedFor = useRef<string | null>(null);

  const [hidden, setHidden] = useState(!working);
  const [wasWorking, setWasWorking] = useState(working);
  if (working !== wasWorking) {
    setWasWorking(working);
    if (working) setHidden(false);
  }

  useEffect(() => {
    if (!working || !progress || !finished) return;
    const job = progress.job_id ?? progress.document_id;
    if (refreshedFor.current === job) return;
    refreshedFor.current = job;
    refresh();
  }, [working, progress, finished, refresh]);

  useEffect(() => {
    if (working || hidden) return;
    const timer = setTimeout(() => setHidden(true), PROGRESS_FADE_MS);
    return () => clearTimeout(timer);
  }, [working, hidden]);

  if (hidden) return null;

  const percent = Math.min(100, Math.max(0, progress?.progress_percent ?? 0));
  const stage = stageLabel(
    progress?.current_stage ?? (document.status === 'queued' ? 'queued' : null)
  );
  const done = (progress?.pages_completed ?? 0) + (progress?.pages_failed ?? 0);
  const label =
    progress && progress.pages_total > 0
      ? `${done} of ${progress.pages_total} pages · ${stage}`
      : stage;

  return (
    <div
      data-testid="processing-progress"
      className={cn(
        'border-b border-slate-200 bg-white px-4 py-2.5 transition-opacity duration-300 sm:px-6',
        working ? 'opacity-100' : 'opacity-0'
      )}
    >
      <div className="flex items-center justify-between gap-4 text-sm">
        <p role="status" className="min-w-0 truncate text-slate-700">
          {label}
        </p>
        <span
          aria-hidden="true"
          className="shrink-0 font-medium text-slate-900 tabular-nums"
        >
          {percent}%
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="Processing progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200"
      >
        <div
          className="h-full rounded-full bg-sky-500 transition-[width] duration-300 ease-out motion-reduce:transition-none"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}
