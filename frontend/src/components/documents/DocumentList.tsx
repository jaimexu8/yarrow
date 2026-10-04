'use client';

import Link from 'next/link';
import { useRef, useState } from 'react';
import { Alert } from '@/components/ui/Alert';
import { StatusBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { parseApiDate, type DocumentSummary } from '@/lib/documents';
import { formatBytes } from '@/lib/uploads';
import { useDocuments } from '@/lib/useDocuments';
import {
  FailureNote,
  RenameForm,
  useCancelProcessing,
  useReprocessDocument,
} from './documentActions';

// Mirrors the server's rules: only finished, failed, or canceled docs.
const REPROCESSABLE = new Set(['failed', 'completed', 'canceled']);

function formatDate(value: string | null): string {
  if (!value) return '';
  return parseApiDate(value).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

/**
 * The signed-in user's documents, newest first, with their processing
 * status. A compact list for the upload page (US-7: "the uploaded document
 * appears in my library"); the dashboard uses DocumentTable instead.
 *
 * Bump ``refreshKey`` to reload, e.g. after an upload finishes.
 */
export function DocumentList({ refreshKey = 0 }: { refreshKey?: number }) {
  const { documents, error, replace } = useDocuments(refreshKey);

  const warning = error && (
    <Alert tone="info">
      Couldn&apos;t refresh your documents ({error}) Trying again…
    </Alert>
  );

  if (documents === null) {
    if (warning) return warning;
    return (
      <div className="flex justify-center py-6 text-slate-500">
        <Spinner label="Loading your documents" />
      </div>
    );
  }
  if (documents.length === 0) {
    return (
      <div className="space-y-3">
        {warning}
        <p className="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-600">
          No documents yet. Uploaded files will appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {warning}
      <ul className="divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white">
        {documents.map((doc) => (
          <DocumentRow key={doc.id} doc={doc} onChanged={replace} />
        ))}
      </ul>
    </div>
  );
}

function DocumentRow({
  doc,
  onChanged,
}: {
  doc: DocumentSummary;
  onChanged: (doc: DocumentSummary) => void;
}) {
  const [editing, setEditing] = useState(false);
  const renameButton = useRef<HTMLButtonElement>(null);
  const { cancel, canceling, cancelError } = useCancelProcessing(
    doc,
    onChanged
  );
  const { reprocess, reprocessing, reprocessError } = useReprocessDocument(
    doc,
    onChanged
  );

  function stopEditing() {
    setEditing(false);
    // Put focus back where the user started.
    requestAnimationFrame(() => renameButton.current?.focus());
  }

  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3">
      <div className="min-w-0 flex-1">
        {editing ? (
          <RenameForm doc={doc} onSaved={onChanged} onDone={stopEditing} />
        ) : (
          <Link
            href={`/documents/${doc.id}`}
            className="block truncate font-medium text-slate-900 underline-offset-4 hover:underline"
          >
            {doc.filename}
          </Link>
        )}
        <p className="text-xs text-slate-500">
          {formatBytes(doc.file_size_bytes)}
          {doc.created_at && <> · Uploaded {formatDate(doc.created_at)}</>}
        </p>
        <FailureNote doc={doc} />
        {cancelError && (
          <p role="alert" className="mt-0.5 text-xs text-red-700">
            {cancelError}
          </p>
        )}
        {reprocessError && (
          <p role="alert" className="mt-0.5 text-xs text-red-700">
            {reprocessError}
          </p>
        )}
      </div>
      <div className="flex items-center gap-3">
        {/* Only while queued: a started or finished job can't be canceled. */}
        {doc.status === 'queued' && !editing && (
          <Button
            variant="link"
            onClick={cancel}
            loading={canceling}
            aria-label={`Cancel processing of ${doc.filename}`}
          >
            Cancel
          </Button>
        )}
        {/* Only once finished, failed, or canceled: the server re-queues a new job. */}
        {!editing && doc.status && REPROCESSABLE.has(doc.status) && (
          <Button
            variant="link"
            onClick={reprocess}
            loading={reprocessing}
            aria-label={`Reprocess ${doc.filename}`}
          >
            Reprocess
          </Button>
        )}
        {!editing && (
          <Button
            ref={renameButton}
            variant="link"
            onClick={() => setEditing(true)}
            aria-label={`Rename ${doc.filename}`}
          >
            Rename
          </Button>
        )}
        <StatusBadge status={doc.status} />
      </div>
    </li>
  );
}
