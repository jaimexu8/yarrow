'use client';

import Link from 'next/link';
import { useEffect, useId, useRef, useState } from 'react';
import { FileText, Pencil, Search, RotateCw, SearchX, Upload, Trash, XCircle } from 'lucide-react';
import { Alert } from '@/components/ui/Alert';
import { ActionMenu, type ActionMenuItem } from '@/components/ui/ActionMenu';
import { StatusBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { isInterrupted, parseApiDate, type DocumentSummary } from '@/lib/documents';
import { cn } from '@/lib/cn';
import { formatBytes } from '@/lib/uploads';
import { useDocuments } from '@/lib/useDocuments';
import {
  DeleteDocumentDialog,
  FailureNote,
  InterruptedNote,
  RenameForm,
  ReprocessLink,
  useReprocessAction,
  useCancelProcessing,
} from './documentActions';

const FILE_TYPE_LABELS: Record<string, string> = {
  'application/pdf': 'PDF',
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
  'image/gif': 'GIF',
};

function fileTypeLabel(doc: DocumentSummary): string {
  const known = FILE_TYPE_LABELS[doc.file_type];
  if (known) return known;
  const dot = doc.filename.lastIndexOf('.');
  return dot > 0 ? doc.filename.slice(dot + 1).toUpperCase() : 'File';
}

/** Styles shared by every body cell, so each row reads as one card. */
const CELL =
  'border-y border-slate-200 bg-white px-3 py-3 sm:px-4 align-middle first:rounded-l-xl first:border-l last:rounded-r-xl last:border-r';
const HEAD = 'px-3 pb-1 text-left sm:px-4 text-xs font-medium text-slate-500';

/**
 * A table of the signed-in user's documents with upload time, page count
 * and processing status. Rows are links to the viewer and offer rename,
 * delete and, while queued, cancel. The list refreshes itself while anything
 * is still processing.
 */
export function DocumentLibrary() {
  const { documents, error, replace, remove, reload } = useDocuments();
  const [query, setQuery] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const filterId = useId();
  const filterInput = useRef<HTMLInputElement>(null);
  const [deletions, setDeletions] = useState(0);

  // After a delete, the row and the menu button that had focus are gone;
  // continue from the search box rather than dropping focus to the top of
  // the page. Runs after React has removed the row.
  useEffect(() => {
    if (deletions > 0) filterInput.current?.focus();
  }, [deletions]);

  function handleDeleted(doc: DocumentSummary) {
    remove(doc.id);
    setAnnouncement(`Deleted ${doc.filename}.`);
    setDeletions((count) => count + 1);
  }

  // Announced even when the library just became empty.
  const status = (
    <p role="status" className="sr-only">
      {announcement}
    </p>
  );

  const warning = error && (
    <Alert tone="info">
      Couldn&apos;t refresh your documents ({error}) Trying again…
    </Alert>
  );

  if (documents === null) {
    return warning || <LoadingRows />;
  }
  if (documents.length === 0) {
    return (
      <div className="space-y-3">
        {status}
        {warning}
        <EmptyLibrary />
      </div>
    );
  }

  const needle = query.trim().toLocaleLowerCase();
  const visible = needle ? documents.filter((doc) =>
    doc.filename.toLocaleLowerCase().includes(needle)
  ) : documents;

  return (
    <div className="space-y-4">
      {status}
      {warning}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-0 flex-1">
          <label htmlFor={filterId} className="sr-only">
            Search documents by name
          </label>
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400"
          />
          <input
            ref={filterInput}
            id={filterId}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search documents…"
            className="w-full rounded-lg border-slate-300 py-2 pl-9 pr-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-900 focus:ring-slate-900"
          />
        </div>
        <p aria-live="polite" className="text-sm text-slate-500">
          {needle
            ? `${visible.length} of ${documents.length} documents`
            : `${documents.length} ${documents.length === 1 ? 'document' : 'documents'}`}
        </p>
      </div>

      {visible.length === 0 ? (
        <NoMatches query={query.trim()} onClear={() => setQuery('')} />
      ) : (
        <table className="w-full border-separate border-spacing-y-2">
          <caption className="sr-only">Your documents, newest first</caption>
          <thead>
            <tr>
              <th scope="col" className={HEAD}>
                Document
              </th>
              <th scope="col" className={cn(HEAD, 'hidden md:table-cell')}>
                Uploaded
              </th>
              <th scope="col" className={cn(HEAD, 'hidden sm:table-cell')}>
                Pages
              </th>
              <th scope="col" className={HEAD}>
                Status
              </th>
              <th scope="col" className={cn(HEAD, 'text-right')}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((doc) => (
              <DocumentTableRow
                key={doc.id}
                doc={doc}
                onChanged={replace}
                onStale={reload}
                onDeleted={handleDeleted}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function UploadedAt({ value }: { value: string | null }) {
  if (!value) return <span className="text-slate-400">—</span>;
  const date = parseApiDate(value);
  return (
    <time dateTime={date.toISOString()} className="block">
      <span className="block text-sm text-slate-700">
        {date.toLocaleDateString(undefined, { dateStyle: 'medium' })}
      </span>
      <span className="block text-xs text-slate-500">
        {date.toLocaleTimeString(undefined, { timeStyle: 'short' })}
      </span>
    </time>
  );
}

function DocumentTableRow({
  doc,
  onChanged,
  onStale,
  onDeleted,
}: {
  doc: DocumentSummary;
  onChanged: (doc: DocumentSummary) => void;

  // Callback when the server indicates that the row is out of date
  onStale: () => void;
  onDeleted: (doc: DocumentSummary) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const nameLink = useRef<HTMLAnchorElement>(null);
  const { cancel, canceling, cancelError } = useCancelProcessing(doc, onChanged);
  const reprocess = useReprocessAction(doc, onChanged, onStale);
  const interrupted = isInterrupted(doc);

  // Inline reprocess link, shown only for incomplete documents
  const reprocessLink = doc.reprocess?.scope === 'incomplete' && (
    <ReprocessLink
      reprocessing={reprocess.reprocessing}
      onClick={reprocess.request}
    />
  );

  function stopEditing() {
    setEditing(false);
    requestAnimationFrame(() => nameLink.current?.focus());
  }

  const actions: ActionMenuItem[] = [
    {
      label: 'Rename',
      icon: Pencil,
      keepFocus: true,
      onSelect: () => setEditing(true),
    },
    // Resumes unfinished work, or processes a finished document again
    ...(reprocess.available && !reprocess.reprocessing
      ? [
        {
          label: 'Reprocess',
          icon: RotateCw,
          keepFocus: doc.reprocess?.scope === 'all',
          onSelect: reprocess.request,
        },
      ]
      : []),
    {
      label: 'Delete',
      icon: Trash,
      keepFocus: true,
      destructive: true,
      onSelect: () => { }, // TODO
    },
  ];
  // Only while queued: a started or finished job can't be canceled.
  if (doc.status === 'queued') {
    actions.push({
      label: 'Cancel processing',
      icon: XCircle,
      destructive: true,
      onSelect: cancel,
    });
  }
  // Last, as the most drastic. The dialog takes focus, and returns it to the
  // menu button if the user backs out.
  actions.push({
    label: 'Delete',
    icon: Trash,
    destructive: true,
    onSelect: () => setConfirmingDelete(true),
  });

  return (
    <tr>
      <td className={cn(CELL, 'w-full max-w-0')}>
        {editing ? (
          <RenameForm doc={doc} onSaved={onChanged} onDone={stopEditing} />
        ) : (
          <div className="flex min-w-0 items-center gap-3">
            <FileText
              aria-hidden="true"
              className="hidden size-5 shrink-0 text-slate-400 sm:block"
            />
            <div className="min-w-0">
              <Link
                ref={nameLink}
                href={`/documents/${doc.id}`}
                className="block truncate text-sm font-medium text-slate-900 underline-offset-4 hover:underline focus-visible:rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
              >
                {doc.filename}
              </Link>
              <p className="truncate text-xs text-slate-500">
                {formatBytes(doc.file_size_bytes)} · {fileTypeLabel(doc)}
                {/* The Uploaded and Pages columns are hidden on small screens. */}
                <span className="sm:hidden">
                  {doc.page_count != null && <> · {doc.page_count} pp.</>}
                </span>
                {doc.created_at && (
                  <span className="md:hidden">
                    {' · '}
                    {parseApiDate(doc.created_at).toLocaleDateString(
                      undefined,
                      {
                        dateStyle: 'medium',
                      }
                    )}
                  </span>
                )}
              </p>
            </div>
          </div>
        )}
        {interrupted ? (
          <InterruptedNote>{reprocessLink}</InterruptedNote>
        ) : (
          <FailureNote doc={doc}>{reprocessLink}</FailureNote>
        )}
        {(cancelError || reprocess.error) && (
          <p role="alert" className="mt-0.5 text-xs text-red-700">
            {reprocess.error ? `Couldn't reprocess: ${reprocess.error}` : cancelError}
          </p>
        )}
        {reprocess.dialog}
      </td>
      <td className={cn(CELL, 'hidden whitespace-nowrap md:table-cell')}>
        <UploadedAt value={doc.created_at} />
      </td>
      <td
        className={cn(
          CELL,
          'hidden whitespace-nowrap text-sm tabular-nums text-slate-700 sm:table-cell'
        )}
      >
        {doc.page_count ?? (
          <>
            <span aria-hidden="true" className="text-slate-400">
              —
            </span>
            <span className="sr-only">Not counted yet</span>
          </>
        )}
      </td>
      <td className={cn(CELL, 'whitespace-nowrap')}>
        <StatusBadge status={doc.status} interrupted={interrupted} />
      </td>
      <td className={cn(CELL, 'whitespace-nowrap')}>
        <div className="flex items-center justify-end gap-1">
          {canceling ? (
            <span className="px-2 text-slate-500">
              <Spinner label="Canceling" />
            </span>
          ) : (
            <Link
              href={`/documents/${doc.id}`}
              className="hidden rounded px-2 py-1 text-sm font-medium text-slate-900 sm:inline underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
            >
              Open<span className="sr-only"> {doc.filename}</span>
            </Link>
          )}
          <ActionMenu
            label={`More actions for ${doc.filename}`}
            items={actions}
          />
        </div>
        <DeleteDocumentDialog
          doc={doc}
          open={confirmingDelete}
          onClose={() => setConfirmingDelete(false)}
          onDeleted={(deleted) => {
            setConfirmingDelete(false);
            onDeleted(deleted);
          }}
        />
      </td>
    </tr>
  );
}

function LoadingRows() {
  return (
    <div aria-busy="true" className="space-y-2">
      <span className="sr-only">
        <Spinner label="Loading your documents" />
      </span>
      {[0, 1, 2].map((row) => (
        <div
          key={row}
          aria-hidden="true"
          className="flex animate-pulse items-center gap-4 rounded-xl border border-slate-200 bg-white px-4 py-4"
        >
          <div className="size-5 rounded bg-slate-100" />
          <div className="flex-1 space-y-2">
            <div className="h-3 w-2/5 rounded bg-slate-200" />
            <div className="h-2.5 w-1/5 rounded bg-slate-100" />
          </div>
          <div className="hidden h-3 w-20 rounded bg-slate-100 md:block" />
          <div className="h-5 w-20 rounded-full bg-slate-100" />
        </div>
      ))}
    </div>
  );
}

function EmptyLibrary() {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center">
      <span className="flex size-11 items-center justify-center rounded-full bg-amber-50 text-amber-600">
        <FileText aria-hidden="true" className="size-5" />
      </span>
      <h2 className="mt-4 text-base font-semibold text-slate-900">
        No documents yet
      </h2>
      <p className="mt-1 max-w-sm text-sm text-slate-600">
        Upload scanned PDFs or images and Yarrow will extract their text and
        tables.
      </p>
      <Link
        href="/upload"
        className="mt-5 inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
      >
        <Upload aria-hidden="true" className="size-4" />
        Upload documents
      </Link>
    </div>
  );
}

function NoMatches({ query, onClear }: { query: string; onClear: () => void }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center">
      <SearchX aria-hidden="true" className="size-5 text-slate-400" />
      <p className="mt-3 text-sm text-slate-700">
        No documents match &ldquo;{query}&rdquo;.
      </p>
      <Button variant="link" className="mt-2 text-sm" onClick={onClear}>
        Clear search
      </Button>
    </div>
  );
}
