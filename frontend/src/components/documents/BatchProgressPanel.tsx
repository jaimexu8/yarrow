'use client';

import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/cn';
import {
  STATUS_LABELS,
  isBatchWorking,
  type BatchProgress,
  type DocumentStatus,
} from '@/lib/documents';
import { useBatchProgress } from '@/lib/useBatchProgress';

const ORDER: DocumentStatus[] = [
  'queued',
  'processing',
  'completed',
  'failed',
  'canceled',
];

const BAR_COLORS: Record<DocumentStatus, string> = {
  queued: 'bg-slate-300',
  processing: 'bg-sky-500',
  completed: 'bg-emerald-500',
  failed: 'bg-red-500',
  canceled: 'bg-amber-400',
};

/**
 * One line summing up the run, e.g. "2 of 5 processed, 1 failed". While
 * files are still being uploaded the server's total is not final yet, so
 * the run is never called finished.
 */
export function batchSummary(
  progress: BatchProgress,
  uploading = false
): string {
  const done = progress.completed + progress.failed + progress.canceled;
  if (progress.total === 0) return 'No files in this upload yet.';
  const parts = [
    uploading
      ? `${done} of ${progress.total} processed, still uploading`
      : isBatchWorking(progress)
        ? `${done} of ${progress.total} processed`
        : `All ${progress.total} ${progress.total === 1 ? 'file' : 'files'} finished`,
  ];
  if (progress.failed) parts.push(`${progress.failed} failed`);
  if (progress.canceled) parts.push(`${progress.canceled} canceled`);
  return parts.join(', ');
}

/**
 * Progress of the files uploaded together in one run (US-31). The counts
 * come from the server and keep updating while any file is still queued or
 * processing, so one failed file is reported next to the ones that finished
 * rather than failing the whole run.
 */
export function BatchProgressPanel({
  batchId,
  refreshKey,
  uploading = false,
  onDismiss,
}: {
  batchId: string;
  refreshKey: number;
  /** Files of this run are still being sent, so the total can grow. */
  uploading?: boolean;
  onDismiss: () => void;
}) {
  const { progress, error, gone } = useBatchProgress(batchId, refreshKey);

  if (gone) return null;

  return (
    <section
      aria-labelledby="batch-heading"
      aria-live="polite"
      className="space-y-3 rounded-xl border border-slate-200 bg-white px-4 py-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="batch-heading" className="font-semibold text-slate-900">
          Processing this upload
        </h2>
        <Button variant="link" className="text-sm" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>

      {progress ? (
        <>
          <p className="text-sm text-slate-600">
            {batchSummary(progress, uploading)}
          </p>
          <div
            role="progressbar"
            aria-label="Files processed"
            aria-valuemin={0}
            aria-valuemax={progress.total}
            aria-valuenow={
              progress.completed + progress.failed + progress.canceled
            }
            className="flex h-2 overflow-hidden rounded-full bg-slate-100"
          >
            {ORDER.map((status) =>
              progress[status] > 0 ? (
                <div
                  key={status}
                  className={cn('h-full', BAR_COLORS[status])}
                  style={{
                    width: `${(progress[status] / progress.total) * 100}%`,
                  }}
                />
              ) : null
            )}
          </div>
          <dl className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
            {ORDER.map((status) => (
              <div key={status} className="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className={cn('h-2 w-2 rounded-full', BAR_COLORS[status])}
                />
                <dt className="text-slate-500">{STATUS_LABELS[status]}</dt>
                <dd className="font-medium tabular-nums text-slate-900">
                  {progress[status]}
                </dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <p className="text-sm text-slate-500">
          {error ? `Could not load progress: ${error}` : 'Loading progress…'}
        </p>
      )}
    </section>
  );
}
