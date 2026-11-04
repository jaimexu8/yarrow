'use client';

import Link from 'next/link';
import {
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type DragEvent,
} from 'react';
import { BatchProgressPanel } from '@/components/documents/BatchProgressPanel';
import { DocumentList } from '@/components/documents/DocumentList';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/cn';
import {
  newBatchId,
  newUploadId,
  uploadDocument,
  type DuplicateOf,
} from '@/lib/documents';
import {
  discardResumable,
  isResumable,
  parseStoredSessions,
  storedSessionsSnapshot,
  subscribeStoredSessions,
  uploadResumable,
} from '@/lib/resumableUpload';
import { storeBatchId, useStoredBatchId } from '@/lib/useBatchProgress';
import {
  ACCEPT_ATTRIBUTE,
  ACCEPTED_TYPES_TEXT,
  MAX_UPLOAD_BYTES,
  checkFile,
  formatBytes,
} from '@/lib/uploads';

type ItemStatus =
  | 'ready' // selected, waiting for "Upload"
  | 'invalid' // failed the checks before upload; will not be sent
  | 'waiting' // in this upload run, not started yet
  | 'uploading'
  | 'paused' // a large file stopped by the user; Resume continues it (US-80)
  | 'accepted' // stored and queued for processing
  | 'stored' // stored, but processing could not be started
  | 'rejected' // the server refused it, with a reason
  | 'duplicate' // identical to a document already in the library (US-79)
  | 'skipped' // a duplicate the user chose not to upload
  | 'failed'; // the upload itself broke; can be retried

type Item = {
  key: string;
  file: File;
  /** Sent with every attempt for this file, so a retry can't duplicate it. */
  uploadId: string;
  /** The run this file was last sent in, so a Retry rejoins that run. */
  batchId?: string;
  status: ItemStatus;
  progress: number;
  message?: string;
  documentId?: string;
  /** The user's own identical document, when the server warned (US-79). */
  duplicateOf?: DuplicateOf;
  /**
   * The user chose "Upload anyway" for this file (US-79). Kept on the item so
   * a Retry after a failed attempt still asks for a copy rather than being
   * warned about the duplicate again; it goes away only with the row.
   */
  allowDuplicate?: boolean;
};

function keyFor(file: File) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function noStoredSessions(): string | null {
  // The server has no localStorage; the client re-reads after hydration.
  return null;
}

/**
 * US-7 Upload PDF, US-8 Bulk upload, US-37 Reject unsupported types,
 * US-31 Batch upload progress, US-79 Detect duplicate uploads,
 * US-80 Resumable uploads.
 *
 * Files are uploaded one at a time, each with its own progress and result,
 * so one bad file never affects the others and a failed one can be retried
 * on its own. Every file of one "Upload" click carries the same batch id, so
 * the server can report how far the run as a whole has got; the id is kept
 * for the tab so coming back to this page picks the run up again.
 *
 * A file identical to one already in the library is not stored; its row
 * names the existing document and offers Skip (nothing is sent) or Upload
 * anyway (sent again, into the same run, as a deliberate extra copy).
 *
 * US-80: files over RESUMABLE_THRESHOLD_BYTES go up in chunks the server
 * confirms one by one, so they can be paused, retried and resumed (even
 * after a reload, by choosing the same file again) from the last confirmed
 * chunk instead of starting over. A large duplicate is detected when its
 * chunks are finalized: Skip then discards the server's chunks, and Upload
 * anyway finalizes the same chunks again with the flag, without resending.
 */
export default function UploadPage() {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  // The run whose progress is shown, remembered for the tab so coming back
  // to this page picks it up again (US-31).
  const batchId = useStoredBatchId();
  // Lets Pause stop the chunked upload in flight, per file.
  const controllers = useRef(new Map<string, AbortController>());
  // Unfinished large uploads remembered from an earlier visit (US-80).
  const unfinished = parseStoredSessions(
    useSyncExternalStore(
      subscribeStoredSessions,
      storedSessionsSnapshot,
      noStoredSessions
    )
  );

  function addFiles(files: FileList | File[]) {
    // Copy the files out now. A FileList belongs to the input or drop event,
    // and it can be empty by the time React runs the updater below (the
    // input is cleared right after this call, and a drop's list expires when
    // the event ends).
    // Ids are made here, not in the updater, which React may run twice.
    const chosen = Array.from(files, (file) => ({
      file,
      uploadId: newUploadId(),
    }));
    setItems((current) => {
      const known = new Set(current.map((item) => item.key));
      const added: Item[] = [];
      for (const { file, uploadId } of chosen) {
        const key = keyFor(file);
        if (known.has(key)) continue;
        known.add(key);
        const problem = checkFile(file);
        added.push({
          key,
          file,
          uploadId,
          status: problem ? 'invalid' : 'ready',
          progress: 0,
          message: problem ?? undefined,
        });
      }
      return [...current, ...added];
    });
  }

  function update(key: string, changes: Partial<Item>) {
    setItems((current) =>
      current.map((item) => (item.key === key ? { ...item, ...changes } : item))
    );
  }

  /**
   * Upload the given files. ``newRun`` starts a fresh batch (the Upload
   * button); a Retry of one file rejoins the run that file was sent in, even
   * if a later run is the one on screen, so every run's counts stay right.
   * ``allowDuplicate`` is "Upload anyway" for a file the server warned is
   * already in the library (US-79); it rejoins its run the same way, and is
   * remembered on the item so every later Retry of it sends the flag too.
   */
  async function uploadAll(
    keys: string[],
    newRun: boolean,
    allowDuplicate = false
  ) {
    if (keys.length === 0) return;
    const freshRun = newBatchId();
    // The server only knows a batch once a file has been accepted, so the
    // progress panel is shown (and the id remembered) from that point.
    let shown = false;
    setUploading(true);
    setItems((current) =>
      current.map((item) =>
        keys.includes(item.key)
          ? {
              ...item,
              status: 'waiting',
              progress: 0,
              message: undefined,
              duplicateOf: undefined,
              allowDuplicate: item.allowDuplicate || allowDuplicate,
            }
          : item
      )
    );
    // One at a time: each file gets its own progress and result, and the
    // server's per-user storage check sees each upload in turn.
    for (const key of keys) {
      const item = items.find((candidate) => candidate.key === key);
      if (!item) continue;
      const runId = newRun ? freshRun : (item.batchId ?? batchId ?? freshRun);
      const askForCopy = allowDuplicate || item.allowDuplicate === true;
      update(key, { status: 'uploading', progress: 0, batchId: runId });
      const onProgress = (progress: number) => update(key, { progress });
      let outcome;
      if (isResumable(item.file)) {
        const controller = new AbortController();
        controllers.current.set(key, controller);
        outcome = await uploadResumable(item.file, {
          batchId: runId,
          onProgress,
          signal: controller.signal,
          allowDuplicate: askForCopy,
        });
        controllers.current.delete(key);
      } else {
        outcome = await uploadDocument(
          item.file,
          item.uploadId,
          onProgress,
          runId,
          askForCopy
        );
      }
      if (outcome.kind === 'paused') {
        update(key, { status: 'paused' });
      } else if (outcome.kind === 'accepted') {
        update(key, {
          status: outcome.processingStarted ? 'accepted' : 'stored',
          progress: 100,
          documentId: outcome.documentId,
          message: outcome.processingStarted ? undefined : outcome.message,
        });
        // A new run takes over the panel. A retry into an earlier run that is
        // no longer on screen leaves the panel showing the current run.
        if (!shown && (newRun || !batchId)) {
          storeBatchId(runId);
          shown = true;
        }
        // The run has one more file; show it in the progress panel now.
        setRefreshKey((n) => n + 1);
      } else if (outcome.kind === 'rejected') {
        update(key, { status: 'rejected', message: outcome.reason });
      } else if (outcome.kind === 'duplicate') {
        update(key, { status: 'duplicate', duplicateOf: outcome.duplicateOf });
      } else {
        update(key, { status: 'failed', message: outcome.message });
      }
    }
    setUploading(false);
    setRefreshKey((n) => n + 1);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (!uploading && event.dataTransfer.files.length) {
      addFiles(event.dataTransfer.files);
    }
  }

  const ready = items.filter((item) => item.status === 'ready');
  const finished = items.filter((item) =>
    [
      'accepted',
      'stored',
      'rejected',
      'duplicate',
      'skipped',
      'failed',
      'paused',
    ].includes(item.status)
  );
  const counts = {
    accepted: items.filter((item) => item.status === 'accepted').length,
    stored: items.filter((item) => item.status === 'stored').length,
    rejected: items.filter((item) =>
      ['rejected', 'invalid'].includes(item.status)
    ).length,
    failed: items.filter((item) => item.status === 'failed').length,
    duplicate: items.filter((item) => item.status === 'duplicate').length,
    skipped: items.filter((item) => item.status === 'skipped').length,
    paused: items.filter((item) => item.status === 'paused').length,
  };
  // Remembered uploads whose file is not in the list yet.
  const resumable = unfinished.filter(
    (stored) =>
      !items.some(
        (item) =>
          item.file.name === stored.filename && item.file.size === stored.size
      )
  );
  const runDone = !uploading && finished.length > 0 && ready.length === 0;

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          Upload documents
        </h1>
        <p className="text-sm text-slate-600">
          Add one or more {ACCEPTED_TYPES_TEXT} files, up to{' '}
          {formatBytes(MAX_UPLOAD_BYTES)} each. Each file is queued for
          processing as soon as it is uploaded. Large files can be paused and
          resumed.
        </p>
      </div>

      {resumable.length > 0 && (
        <Alert tone="info">
          {resumable.length === 1
            ? 'An upload did not finish: '
            : `${resumable.length} uploads did not finish: `}
          {resumable.map((stored) => stored.filename).join(', ')}. Choose the
          same {resumable.length === 1 ? 'file' : 'files'} again to continue
          from where {resumable.length === 1 ? 'it' : 'they'} stopped.
        </Alert>
      )}

      <section aria-labelledby="choose-heading" className="space-y-4">
        <h2 id="choose-heading" className="sr-only">
          Choose files
        </h2>
        <div
          onDragOver={(event) => {
            event.preventDefault();
            if (!uploading) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cn(
            'flex flex-col items-center gap-3 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors',
            dragging
              ? 'border-slate-900 bg-slate-50'
              : 'border-slate-300 bg-white'
          )}
        >
          <p className="text-sm text-slate-600">
            Drag files here, or choose them from your computer.
          </p>
          <input
            ref={inputRef}
            id={inputId}
            type="file"
            multiple
            accept={ACCEPT_ATTRIBUTE}
            disabled={uploading}
            className="peer sr-only"
            onChange={(event) => {
              if (event.target.files) addFiles(event.target.files);
              // Allow choosing the same file again after removing it.
              event.target.value = '';
            }}
          />
          <label
            htmlFor={inputId}
            className={cn(
              'cursor-pointer rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50',
              'peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-slate-900',
              'peer-disabled:cursor-not-allowed peer-disabled:text-slate-400'
            )}
          >
            Choose files
          </label>
        </div>

        {items.length > 0 && (
          <ul className="divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white">
            {items.map((item) => (
              <FileRow
                key={item.key}
                item={item}
                disabled={uploading}
                onRemove={() => {
                  if (isResumable(item.file)) void discardResumable(item.file);
                  setItems((current) =>
                    current.filter((other) => other.key !== item.key)
                  );
                }}
                onRetry={() => uploadAll([item.key], false)}
                onSkip={() => {
                  // A large duplicate's chunks are still on the server,
                  // waiting for Upload anyway; Skip discards them (US-80).
                  if (isResumable(item.file)) void discardResumable(item.file);
                  update(item.key, { status: 'skipped', progress: 0 });
                }}
                onUploadAnyway={() => uploadAll([item.key], false, true)}
                onPause={() => controllers.current.get(item.key)?.abort()}
              />
            ))}
          </ul>
        )}

        {/* Announced to screen readers as results come in. */}
        <div aria-live="polite">
          {runDone && (
            <Alert
              tone={
                counts.failed ||
                counts.rejected ||
                counts.stored ||
                counts.duplicate ||
                counts.skipped
                  ? 'info'
                  : 'success'
              }
            >
              {summaryText(counts)}
            </Alert>
          )}
        </div>

        <div className="flex flex-wrap gap-3">
          <Button
            className="w-auto px-5"
            disabled={ready.length === 0}
            loading={uploading}
            onClick={() =>
              uploadAll(
                ready.map((item) => item.key),
                true
              )
            }
          >
            {uploadButtonLabel(uploading, ready.length)}
          </Button>
          {items.length > 0 && !uploading && (
            <Button
              variant="secondary"
              className="w-auto px-5"
              onClick={() => setItems([])}
            >
              Clear list
            </Button>
          )}
        </div>
      </section>

      {batchId && (
        <BatchProgressPanel
          batchId={batchId}
          refreshKey={refreshKey}
          uploading={uploading}
          onDismiss={() => storeBatchId(null)}
        />
      )}

      <section aria-labelledby="library-heading" className="space-y-3">
        <div className="flex items-baseline justify-between">
          <h2
            id="library-heading"
            className="text-lg font-semibold text-slate-900"
          >
            Your documents
          </h2>
          <Link
            href="/dashboard"
            className="text-sm font-medium text-slate-900 underline underline-offset-4"
          >
            Open library
          </Link>
        </div>
        <DocumentList refreshKey={refreshKey} />
      </section>
    </div>
  );
}

function uploadButtonLabel(uploading: boolean, readyCount: number): string {
  if (uploading) return 'Uploading…';
  if (readyCount === 0) return 'Upload files';
  return readyCount === 1 ? 'Upload 1 file' : `Upload ${readyCount} files`;
}

function summaryText(counts: {
  accepted: number;
  stored: number;
  rejected: number;
  failed: number;
  duplicate: number;
  skipped: number;
  paused: number;
}): string {
  const parts = [
    `${counts.accepted} ${counts.accepted === 1 ? 'file' : 'files'} uploaded and queued for processing`,
  ];
  if (counts.stored) {
    parts.push(`${counts.stored} uploaded but not yet processing`);
  }
  if (counts.rejected) parts.push(`${counts.rejected} not accepted`);
  if (counts.duplicate) {
    parts.push(`${counts.duplicate} already in your library`);
  }
  if (counts.skipped) parts.push(`${counts.skipped} skipped`);
  if (counts.failed) parts.push(`${counts.failed} failed to upload`);
  if (counts.paused) parts.push(`${counts.paused} paused`);
  return `${parts.join(', ')}.`;
}

const STATUS_TEXT: Record<ItemStatus, string> = {
  ready: 'Ready to upload',
  invalid: 'Not accepted',
  waiting: 'Waiting',
  uploading: 'Uploading',
  paused: 'Paused',
  accepted: 'Uploaded, queued for processing',
  stored: 'Uploaded, but processing could not start',
  rejected: 'Not accepted',
  duplicate: 'Already in your library',
  skipped: 'Skipped',
  failed: 'Upload failed',
};

function FileRow({
  item,
  disabled,
  onRemove,
  onRetry,
  onSkip,
  onUploadAnyway,
  onPause,
}: {
  item: Item;
  disabled: boolean;
  onRemove: () => void;
  onRetry: () => void;
  onSkip: () => void;
  onUploadAnyway: () => void;
  onPause: () => void;
}) {
  const chunked = isResumable(item.file);
  const problem = ['invalid', 'stored', 'rejected', 'failed'].includes(
    item.status
  );
  return (
    <li className="space-y-2 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-900">
            {item.documentId ? (
              <Link
                href={`/documents/${item.documentId}`}
                className="underline-offset-4 hover:underline"
              >
                {item.file.name}
              </Link>
            ) : (
              item.file.name
            )}
          </p>
          <p
            className={cn(
              'text-xs',
              problem ? 'text-red-700' : 'text-slate-500'
            )}
          >
            {formatBytes(item.file.size)} · {STATUS_TEXT[item.status]}
            {item.message && <>: {item.message}</>}
          </p>
          {item.status === 'duplicate' && item.duplicateOf && (
            <p className="text-xs text-amber-800">
              This file is identical to{' '}
              <Link
                href={`/documents/${item.duplicateOf.documentId}`}
                className="font-medium underline underline-offset-4"
              >
                {item.duplicateOf.filename}
              </Link>
              , already in your library. Skip it, or upload another copy.
            </p>
          )}
          {item.status === 'skipped' && item.duplicateOf && (
            <p className="text-xs text-slate-500">
              Not uploaded; you already have{' '}
              <Link
                href={`/documents/${item.duplicateOf.documentId}`}
                className="underline underline-offset-4"
              >
                {item.duplicateOf.filename}
              </Link>
              .
            </p>
          )}
        </div>
        <div className="flex gap-3">
          {item.status === 'duplicate' && (
            <>
              <Button
                variant="link"
                onClick={onSkip}
                disabled={disabled}
                aria-label={`Skip ${item.file.name}`}
              >
                Skip
              </Button>
              <Button
                variant="link"
                onClick={onUploadAnyway}
                disabled={disabled}
                aria-label={`Upload ${item.file.name} anyway`}
              >
                Upload anyway
              </Button>
            </>
          )}
          {chunked && item.status === 'uploading' && (
            // Not disabled while uploading: pausing is what it is for.
            <Button
              variant="link"
              onClick={onPause}
              aria-label={`Pause ${item.file.name}`}
            >
              Pause
            </Button>
          )}
          {item.status === 'paused' && (
            <Button
              variant="link"
              onClick={onRetry}
              disabled={disabled}
              aria-label={`Resume ${item.file.name}`}
            >
              Resume
            </Button>
          )}
          {item.status === 'failed' && (
            <Button
              variant="link"
              onClick={onRetry}
              disabled={disabled}
              aria-label={`Retry ${item.file.name}`}
            >
              Retry
            </Button>
          )}
          {[
            'ready',
            'invalid',
            'rejected',
            'skipped',
            'failed',
            'paused',
          ].includes(item.status) && (
            <Button
              variant="link"
              onClick={onRemove}
              disabled={disabled}
              aria-label={`Remove ${item.file.name}`}
            >
              Remove
            </Button>
          )}
        </div>
      </div>
      {['uploading', 'waiting', 'paused'].includes(item.status) && (
        <div
          role="progressbar"
          aria-label={`Uploading ${item.file.name}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={item.progress}
          className="h-1.5 overflow-hidden rounded-full bg-slate-100"
        >
          <div
            className="h-full rounded-full bg-slate-900 transition-[width]"
            style={{ width: `${item.progress}%` }}
          />
        </div>
      )}
    </li>
  );
}
