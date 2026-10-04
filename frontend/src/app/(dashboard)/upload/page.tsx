'use client';

import Link from 'next/link';
import { useId, useRef, useState, type DragEvent } from 'react';
import { DocumentList } from '@/components/documents/DocumentList';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/cn';
import { newUploadId, uploadDocument } from '@/lib/documents';
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
  | 'accepted' // stored and queued for processing
  | 'stored' // stored, but processing could not be started
  | 'rejected' // the server refused it, with a reason
  | 'failed'; // the upload itself broke; can be retried

type Item = {
  key: string;
  file: File;
  /** Sent with every attempt for this file, so a retry can't duplicate it. */
  uploadId: string;
  status: ItemStatus;
  progress: number;
  message?: string;
  documentId?: string;
};

function keyFor(file: File) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

/**
 * US-7 Upload PDF, US-8 Bulk upload, US-37 Reject unsupported types.
 *
 * Files are uploaded one at a time, each with its own progress and result,
 * so one bad file never affects the others and a failed one can be retried
 * on its own.
 */
export default function UploadPage() {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

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

  async function uploadAll(keys: string[]) {
    if (keys.length === 0) return;
    setUploading(true);
    setItems((current) =>
      current.map((item) =>
        keys.includes(item.key)
          ? { ...item, status: 'waiting', progress: 0, message: undefined }
          : item
      )
    );
    // One at a time: each file gets its own progress and result, and the
    // server's per-user storage check sees each upload in turn.
    for (const key of keys) {
      const item = items.find((candidate) => candidate.key === key);
      if (!item) continue;
      update(key, { status: 'uploading', progress: 0 });
      const outcome = await uploadDocument(
        item.file,
        item.uploadId,
        (progress) => update(key, { progress })
      );
      if (outcome.kind === 'accepted') {
        update(key, {
          status: outcome.processingStarted ? 'accepted' : 'stored',
          progress: 100,
          documentId: outcome.documentId,
          message: outcome.processingStarted ? undefined : outcome.message,
        });
      } else if (outcome.kind === 'rejected') {
        update(key, { status: 'rejected', message: outcome.reason });
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
    ['accepted', 'stored', 'rejected', 'failed'].includes(item.status)
  );
  const counts = {
    accepted: items.filter((item) => item.status === 'accepted').length,
    stored: items.filter((item) => item.status === 'stored').length,
    rejected: items.filter((item) =>
      ['rejected', 'invalid'].includes(item.status)
    ).length,
    failed: items.filter((item) => item.status === 'failed').length,
  };
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
          processing as soon as it is uploaded.
        </p>
      </div>

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
                onRemove={() =>
                  setItems((current) =>
                    current.filter((other) => other.key !== item.key)
                  )
                }
                onRetry={() => uploadAll([item.key])}
              />
            ))}
          </ul>
        )}

        {/* Announced to screen readers as results come in. */}
        <div aria-live="polite">
          {runDone && (
            <Alert
              tone={
                counts.failed || counts.rejected || counts.stored
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
            onClick={() => uploadAll(ready.map((item) => item.key))}
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
}): string {
  const parts = [
    `${counts.accepted} ${counts.accepted === 1 ? 'file' : 'files'} uploaded and queued for processing`,
  ];
  if (counts.stored) {
    parts.push(`${counts.stored} uploaded but not yet processing`);
  }
  if (counts.rejected) parts.push(`${counts.rejected} not accepted`);
  if (counts.failed) parts.push(`${counts.failed} failed to upload`);
  return `${parts.join(', ')}.`;
}

const STATUS_TEXT: Record<ItemStatus, string> = {
  ready: 'Ready to upload',
  invalid: 'Not accepted',
  waiting: 'Waiting',
  uploading: 'Uploading',
  accepted: 'Uploaded, queued for processing',
  stored: 'Uploaded, but processing could not start',
  rejected: 'Not accepted',
  failed: 'Upload failed',
};

function FileRow({
  item,
  disabled,
  onRemove,
  onRetry,
}: {
  item: Item;
  disabled: boolean;
  onRemove: () => void;
  onRetry: () => void;
}) {
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
        </div>
        <div className="flex gap-3">
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
          {['ready', 'invalid', 'rejected', 'failed'].includes(item.status) && (
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
      {(item.status === 'uploading' || item.status === 'waiting') && (
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
