'use client';

import Link from 'next/link';
import {
  useEffect,
  useId,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import {
  ArrowDown,
  ArrowUp,
  Check,
  FileText,
  Pencil,
  RotateCw,
  Search,
  SearchX,
  Upload,
  Trash,
  X,
  XCircle,
} from 'lucide-react';
import { Alert } from '@/components/ui/Alert';
import { ActionMenu, type ActionMenuItem } from '@/components/ui/ActionMenu';
import { StatusBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import {
  bulkDelete,
  bulkReprocess,
  canCancel,
  isInterrupted,
  parseApiDate,
  STATUS_LABELS,
  type DocumentStatus,
  type DocumentSummary,
} from '@/lib/documents';
import { cn } from '@/lib/cn';
import { formatBytes } from '@/lib/uploads';
import { toApiError } from '@/lib/errors';
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

// Lifecycle order, used for the status filter's option list.
const STATUS_OPTIONS: DocumentStatus[] = [
  'queued',
  'processing',
  'completed',
  'failed',
  'canceled',
];

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

type SortColumn = 'name' | 'date' | 'status' | 'pages';
type SortDirection = 'asc' | 'desc';

/**
 * A table of the signed-in user's documents with upload time, page count
 * and processing status. Rows are links to the viewer and offer rename,
 * delete and, while queued, cancel. The list refreshes itself while anything
 * is still processing.
 */
export function DocumentLibrary() {
  const { documents, error, replace, remove, reload } = useDocuments();
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<DocumentStatus | ''>('');
  const [sortCol, setSortCol] = useState<SortColumn>('date');
  const [sortDir, setSortDir] = useState<SortDirection>('desc');
  const [announcement, setAnnouncement] = useState('');
  const filterId = useId();
  const filterInput = useRef<HTMLInputElement>(null);
  const [deletions, setDeletions] = useState(0);

  // Row selection for bulk actions. A plain Set, plus the last row that was
  // toggled so Shift+click can range-select from it.
  const [selected, setSelected] = useState<ReadonlySet<string>>(
    () => new Set()
  );
  const [anchor, setAnchor] = useState<string | null>(null);

  // After a delete, the row and the menu button that had focus are gone;
  // continue from the search box rather than dropping focus to the top of
  // the page. Runs after React has removed the row.
  useEffect(() => {
    if (deletions > 0) filterInput.current?.focus();
  }, [deletions]);

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Toggles one row. A plain click only changes that row; Shift+click
  // extends the selection to cover every row between it and the anchor,
  // inclusive (all of them end up selected, like a file manager).
  function toggleRow(id: string, event: ReactMouseEvent) {
    setAnchor(id);
    if (!event.shiftKey) {
      toggleSelected(id);
      return;
    }
    const from = sorted.findIndex((d) => d.id === anchor);
    const to = sorted.findIndex((d) => d.id === id);
    if (from < 0 || to < 0) {
      toggleSelected(id);
      return;
    }
    const [lo, hi] = from < to ? [from, to] : [to, from];
    const range = sorted.slice(lo, hi + 1).map((d) => d.id);
    setSelected((prev) => {
      const next = new Set(prev);
      for (const rowId of range) next.add(rowId);
      return next;
    });
  }

  function toggleSort(col: SortColumn) {
    if (sortCol === col) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(col);
      setSortDir(col === 'date' ? 'desc' : 'asc');
    }
  }

  function handleDeleted(doc: DocumentSummary) {
    remove(doc.id);
    dropSelected([doc.id]);
    setAnnouncement(`Deleted ${doc.filename}.`);
    setDeletions((count) => count + 1);
  }

  // Removes rows that are no longer selectable from the current selection.
  // Used after deletes so a stale id does not keep a bulk action armed.
  function dropSelected(ids: string[]) {
    if (ids.length === 0) return;
    const gone = new Set(ids);
    setSelected((prev) => {
      if (![...prev].some((id) => gone.has(id))) return prev;
      const next = new Set(prev);
      for (const id of gone) next.delete(id);
      return next;
    });
    if (anchor !== null && gone.has(anchor)) setAnchor(null);
  }

  const [bulk, setBulk] = useState<{
    action: 'delete' | 'reprocess';
    ids: string[];
  } | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  function requestBulk(action: 'delete' | 'reprocess') {
    if (selected.size === 0) return;
    setBulk({ action, ids: [...selected] });
    setBulkError(null);
  }

  function clearSelection() {
    setSelected(() => new Set());
    setAnchor(null);
  }

  async function runBulk() {
    if (!bulk) return;
    const { action, ids } = bulk;
    setBulkBusy(true);
    setBulkError(null);
    try {
      const results =
        action === 'delete' ? await bulkDelete(ids) : await bulkReprocess(ids);
      const okIds: string[] = [];
      for (const item of results) {
        if (item.ok) okIds.push(item.document_id);
      }
      if (action === 'delete') {
        for (const id of okIds) remove(id);
      }
      // Whatever was acted on leaves the selection; the rest stay so the
      // user can retry or keep them.
      dropSelected(okIds);
      setBulk(null);
    } catch (err) {
      setBulkError(toApiError(err).message);
    } finally {
      setBulkBusy(false);
    }
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
  const visible = documents.filter(
    (doc) =>
      (statusFilter === '' || doc.status === statusFilter) &&
      (!needle || doc.filename.toLocaleLowerCase().includes(needle))
  );

  const sorted = [...visible].sort((a, b) => {
    let cmp = 0;
    if (sortCol === 'name') {
      cmp = a.filename.localeCompare(b.filename);
    } else if (sortCol === 'date') {
      const dateA = a.created_at ?? '';
      const dateB = b.created_at ?? '';
      cmp = dateA.localeCompare(dateB);
    } else if (sortCol === 'status') {
      const statA = a.status ?? '';
      const statB = b.status ?? '';
      cmp = statA.localeCompare(statB);
    } else if (sortCol === 'pages') {
      const pagesA = a.page_count ?? 0;
      const pagesB = b.page_count ?? 0;
      cmp = pagesA - pagesB;
    }
    return sortDir === 'asc' ? cmp : -cmp;
  });

  return (
    <div className="space-y-4">
      {status}
      {warning}
      {bulk && (
        <BulkConfirmDialog
          action={bulk.action}
          count={bulk.ids.length}
          busy={bulkBusy}
          error={bulkError}
          onConfirm={runBulk}
          onClose={() => setBulk(null)}
        />
      )}
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
        <div className="w-44 shrink-0">
          <Select
            id="status-filter"
            label="Status"
            hideLabel
            value={statusFilter}
            onChange={(event) =>
              setStatusFilter(event.target.value as DocumentStatus | '')
            }
          >
            <option value="">Any status</option>
            {STATUS_OPTIONS.map((status) => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
          </Select>
        </div>
        <p aria-live="polite" className="text-sm text-slate-500">
          {needle || statusFilter !== ''
            ? `${visible.length} of ${documents.length} documents`
            : `${documents.length} ${documents.length === 1 ? 'document' : 'documents'}`}
        </p>
      </div>

      {visible.length === 0 ? (
        <NoMatches
          query={query.trim()}
          statusLabel={statusFilter ? STATUS_LABELS[statusFilter] : null}
          onClear={() => {
            setQuery('');
            setStatusFilter('');
          }}
        />
      ) : (
        <>
          {selected.size > 0 && (
            <BulkActionBar
              count={selected.size}
              onClear={clearSelection}
              onDelete={() => requestBulk('delete')}
              onReprocess={() => requestBulk('reprocess')}
            />
          )}
          <table className="w-full border-separate border-spacing-y-2">
            <caption className="sr-only">Your documents, newest first</caption>
            <thead>
              <tr>
                <th scope="col" className={cn(HEAD, 'w-8')}>
                  <SelectAllButton
                    indeterminate={
                      selected.size > 0 && selected.size < visible.length
                    }
                    checked={
                      visible.length > 0 && selected.size === visible.length
                    }
                    onToggle={() => {
                      if (
                        visible.length > 0 &&
                        selected.size === visible.length
                      ) {
                        clearSelection();
                      } else {
                        setSelected(() => new Set(sorted.map((d) => d.id)));
                      }
                    }}
                  />
                </th>
                <th scope="col" className={HEAD}>
                  <button
                    className="group inline-flex items-center gap-x-1 font-medium hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 rounded"
                    onClick={() => toggleSort('name')}
                  >
                    Document
                    {sortCol === 'name' &&
                      (sortDir === 'asc' ? (
                        <ArrowUp className="size-4" />
                      ) : (
                        <ArrowDown className="size-4" />
                      ))}
                  </button>
                </th>
                <th scope="col" className={cn(HEAD, 'hidden md:table-cell')}>
                  <button
                    className="group inline-flex items-center gap-x-1 font-medium hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 rounded"
                    onClick={() => toggleSort('date')}
                  >
                    Uploaded
                    {sortCol === 'date' &&
                      (sortDir === 'asc' ? (
                        <ArrowUp className="size-4" />
                      ) : (
                        <ArrowDown className="size-4" />
                      ))}
                  </button>
                </th>
                <th scope="col" className={cn(HEAD, 'hidden sm:table-cell')}>
                  <button
                    className="group inline-flex items-center gap-x-1 font-medium hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 rounded"
                    onClick={() => toggleSort('pages')}
                  >
                    Pages
                    {sortCol === 'pages' &&
                      (sortDir === 'asc' ? (
                        <ArrowUp className="size-4" />
                      ) : (
                        <ArrowDown className="size-4" />
                      ))}
                  </button>
                </th>
                <th scope="col" className={HEAD}>
                  <button
                    className="group inline-flex items-center gap-x-1 font-medium hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 rounded"
                    onClick={() => toggleSort('status')}
                  >
                    Status
                    {sortCol === 'status' &&
                      (sortDir === 'asc' ? (
                        <ArrowUp className="size-4" />
                      ) : (
                        <ArrowDown className="size-4" />
                      ))}
                  </button>
                </th>
                <th scope="col" className={cn(HEAD, 'text-right')}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((doc) => (
                <DocumentTableRow
                  key={doc.id}
                  doc={doc}
                  selected={selected.has(doc.id)}
                  onToggle={(event) => toggleRow(doc.id, event)}
                  onChanged={replace}
                  onStale={reload}
                  onDeleted={handleDeleted}
                />
              ))}
            </tbody>
          </table>
        </>
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
  selected,
  onToggle,
  onChanged,
  onStale,
  onDeleted,
}: {
  doc: DocumentSummary;
  selected: boolean;
  onToggle: (event: ReactMouseEvent) => void;
  onChanged: (doc: DocumentSummary) => void;

  // Callback when the server indicates that the row is out of date
  onStale: () => void;
  onDeleted: (doc: DocumentSummary) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const nameLink = useRef<HTMLAnchorElement>(null);
  const { cancel, canceling, cancelError } = useCancelProcessing(
    doc,
    onChanged
  );
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
  ];
  // Only until it finishes: a completed or failed job can't be canceled.
  if (canCancel(doc)) {
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
      <td className={cn(CELL, 'w-8 pl-2.5 sm:pl-3')}>
        <button
          type="button"
          role="checkbox"
          aria-checked={selected}
          aria-label={`Select ${doc.filename}`}
          onClick={(event) => onToggle(event)}
          className={cn(
            'flex size-5 items-center justify-center rounded border transition-colors',
            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900',
            selected
              ? 'border-slate-900 bg-slate-900 text-white'
              : 'border-slate-300 bg-white text-transparent hover:border-slate-400'
          )}
        >
          <Check aria-hidden="true" className="size-3.5" />
        </button>
      </td>
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
            {reprocess.error
              ? `Couldn't reprocess: ${reprocess.error}`
              : cancelError}
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

/** A checkbox that selects or clears every row in the current view. */
function SelectAllButton({
  checked,
  indeterminate,
  onToggle,
}: {
  checked: boolean;
  indeterminate: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={indeterminate ? 'mixed' : checked}
      aria-label="Select all documents"
      onClick={onToggle}
      className={cn(
        'flex size-5 items-center justify-center rounded border transition-colors',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900',
        checked || indeterminate
          ? 'border-slate-900 bg-slate-900 text-white'
          : 'border-slate-300 bg-white text-transparent hover:border-slate-400'
      )}
    >
      {indeterminate ? (
        <span className="h-0.5 w-2.5 rounded bg-white" />
      ) : (
        <Check aria-hidden="true" className="size-3.5" />
      )}
    </button>
  );
}

/**
 * Shown in place of the usual header area while at least one row is selected.
 * It replaces the "Upload documents" action with bulk delete and reprocess.
 */
function BulkActionBar({
  count,
  onClear,
  onDelete,
  onReprocess,
}: {
  count: number;
  onClear: () => void;
  onDelete: () => void;
  onReprocess: () => void;
}) {
  return (
    <div
      role="group"
      aria-label="Actions for the selected documents"
      className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-900 bg-slate-900 px-3 py-2 text-white"
    >
      <p className="text-sm font-medium">
        {count} {count === 1 ? 'document' : 'documents'} selected
      </p>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <Button
          variant="secondary"
          className="w-auto px-3"
          onClick={onReprocess}
        >
          <RotateCw aria-hidden="true" className="size-4" />
          Reprocess
        </Button>
        <Button variant="danger" className="w-auto px-3" onClick={onDelete}>
          <Trash aria-hidden="true" className="size-4" />
          Delete
        </Button>
        <button
          type="button"
          onClick={onClear}
          aria-label="Clear selection"
          className="rounded-md p-1.5 text-slate-300 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </div>
    </div>
  );
}

/** Confirms a bulk delete or reprocess before the request is sent. */
function BulkConfirmDialog({
  action,
  count,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  action: 'delete' | 'reprocess';
  count: number;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const isDelete = action === 'delete';
  const title = isDelete
    ? `Delete ${count} ${count === 1 ? 'document' : 'documents'}?`
    : `Reprocess ${count} ${count === 1 ? 'document' : 'documents'}?`;
  const body = isDelete
    ? `These ${count} ${
        count === 1 ? 'document' : 'documents'
      } and everything extracted from them will be permanently deleted. This can't be undone.`
    : `Each selected document will be processed again. Documents that are already being processed are skipped and stay selected.`;
  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      title={title}
      footer={
        <>
          <Button
            variant="secondary"
            className="sm:w-auto"
            onClick={onClose}
            disabled={busy}
            data-autofocus
          >
            Cancel
          </Button>
          <Button
            variant={isDelete ? 'danger' : 'primary'}
            className="sm:w-auto"
            onClick={onConfirm}
            loading={busy}
          >
            {error ? 'Try again' : isDelete ? 'Delete' : 'Reprocess'}
          </Button>
        </>
      }
    >
      <p className="break-words">{body}</p>
      {error && (
        <Alert className="mt-4" tone="error">
          {error}
        </Alert>
      )}
    </Modal>
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

function NoMatches({
  query,
  statusLabel,
  onClear,
}: {
  query: string;
  statusLabel: string | null;
  onClear: () => void;
}) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center">
      <SearchX aria-hidden="true" className="size-5 text-slate-400" />
      <p className="mt-3 text-sm text-slate-700">
        {query && statusLabel ? (
          <>
            No documents match &ldquo;{query}&rdquo; with status {statusLabel}.
          </>
        ) : query ? (
          <>No documents match &ldquo;{query}&rdquo;.</>
        ) : (
          <>No documents with status {statusLabel}.</>
        )}
      </p>
      <Button variant="link" className="mt-2 text-sm" onClick={onClear}>
        Clear filters
      </Button>
    </div>
  );
}
