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
  Folder,
  FolderInput,
  FolderOpen,
  FolderPlus,
  Pencil,
  RotateCw,
  Search,
  SearchX,
  Tag as TagIcon,
  Upload,
  Trash,
  X,
  XCircle,
} from 'lucide-react';
import { Alert } from '@/components/ui/Alert';
import { ActionMenu, type ActionMenuItem } from '@/components/ui/ActionMenu';
import { StatusBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import {
  bulkDelete,
  bulkReprocess,
  canCancel,
  isInterrupted,
  moveDocumentToFolder,
  parseApiDate,
  STATUS_LABELS,
  type DocumentStatus,
  type DocumentSummary,
  type DocumentTag,
} from '@/lib/documents';
import {
  createFolder,
  deleteFolder,
  listFolders,
  renameFolder,
  type Folder as LibraryFolder,
} from '@/lib/folders';
import { cn } from '@/lib/cn';
import { formatBytes } from '@/lib/storage';
import { toApiError } from '@/lib/errors';
import { useDocuments } from '@/lib/useDocuments';
import {
  addDocumentTag,
  documentHasTag,
  MAX_TAG_NAME_LENGTH,
  removeDocumentTag,
  tagNameError,
  uniqueTags,
} from '@/lib/tags';
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

// lifecycle order, used for the status filter's option list.
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

/** styles shared by every body cell, so each row reads as one card. */
const CELL =
  'border-y border-slate-200 bg-white px-3 py-3 sm:px-4 align-middle first:rounded-l-xl first:border-l last:rounded-r-xl last:border-r';
const HEAD = 'px-3 pb-1 text-left sm:px-4 text-xs font-medium text-slate-500';

type SortColumn = 'name' | 'date' | 'status' | 'pages';
type SortDirection = 'asc' | 'desc';
type FolderSelection = 'all' | 'unfiled' | string;

function inSelectedFolder(
  doc: DocumentSummary,
  selected: FolderSelection
): boolean {
  if (selected === 'all') return true;
  if (selected === 'unfiled') return doc.folder_id == null;
  return doc.folder_id === selected;
}

/**
 * a table of the signed-in user's documents with upload time, page count
 * and processing status. rows are links to the viewer and offer rename,
 * delete and, while queued, cancel. the list refreshes itself while anything
 * is still processing.
 */
export function DocumentLibrary() {
  const { documents, error, replace, remove, reload } = useDocuments();
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<DocumentStatus | ''>('');
  const [tagFilter, setTagFilter] = useState('');
  const [sortCol, setSortCol] = useState<SortColumn>('date');
  const [sortDir, setSortDir] = useState<SortDirection>('desc');
  const [announcement, setAnnouncement] = useState('');
  const filterId = useId();
  const filterInput = useRef<HTMLInputElement>(null);
  const [deletions, setDeletions] = useState(0);
  const [folders, setFolders] = useState<LibraryFolder[]>([]);
  const [foldersError, setFoldersError] = useState<string | null>(null);
  const [currentFolder, setCurrentFolder] = useState<FolderSelection>('all');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [renamingFolder, setRenamingFolder] = useState<LibraryFolder | null>(
    null
  );
  const [deletingFolder, setDeletingFolder] = useState<LibraryFolder | null>(
    null
  );

  useEffect(() => {
    let current = true;
    listFolders()
      .then((items) => {
        if (current) {
          setFolders(items);
          setFoldersError(null);
        }
      })
      .catch((err) => {
        if (current) setFoldersError(toApiError(err).message);
      });
    return () => {
      current = false;
    };
  }, []);

  // row selection for bulk actions. a plain set, plus the last row that was
  // toggled so shift+click can range-select from it.
  const [selected, setSelected] = useState<ReadonlySet<string>>(
    () => new Set()
  );
  const [anchor, setAnchor] = useState<string | null>(null);

  // after a delete, the row and the menu button that had focus are gone;
  // continue from the search box rather than dropping focus to the top of
  // the page. runs after react has removed the row.
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

  // toggles one row. a plain click only changes that row; shift+click
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

  // removes rows that are no longer selectable from the current selection.
  // used after deletes so a stale id does not keep a bulk action armed.
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
      // whatever was acted on leaves the selection; the rest stay so the
      // user can retry or keep them.
      dropSelected(okIds);
      setBulk(null);
    } catch (err) {
      setBulkError(toApiError(err).message);
    } finally {
      setBulkBusy(false);
    }
  }

  // announced even when the library just became empty.
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
  const folderWarning = foldersError && (
    <Alert tone="error">{foldersError}</Alert>
  );

  function handleFolderCreated(folder: LibraryFolder) {
    setFolders((current) =>
      [...current, folder].sort((a, b) => a.name.localeCompare(b.name))
    );
    setCurrentFolder(folder.id);
    setCreatingFolder(false);
    setAnnouncement(`Created folder ${folder.name}.`);
  }

  function handleFolderRenamed(folder: LibraryFolder) {
    setFolders((current) =>
      current
        .map((item) => (item.id === folder.id ? folder : item))
        .sort((a, b) => a.name.localeCompare(b.name))
    );
    setRenamingFolder(null);
    setAnnouncement(`Renamed folder to ${folder.name}.`);
  }

  function handleFolderDeleted(folder: LibraryFolder) {
    setFolders((current) => current.filter((item) => item.id !== folder.id));
    if (currentFolder === folder.id) setCurrentFolder('unfiled');
    setDeletingFolder(null);
    setAnnouncement(`Deleted folder ${folder.name}. Documents are unfiled.`);
    reload();
  }

  const folderChrome = (
    <>
      {creatingFolder && (
        <FolderNameDialog
          title="New folder"
          confirmLabel="Create"
          onSave={createFolder}
          onSaved={handleFolderCreated}
          onClose={() => setCreatingFolder(false)}
        />
      )}
      {renamingFolder && (
        <FolderNameDialog
          title="Rename folder"
          confirmLabel="Save"
          initialName={renamingFolder.name}
          onSave={(name) => renameFolder(renamingFolder.id, name)}
          onSaved={handleFolderRenamed}
          onClose={() => setRenamingFolder(null)}
        />
      )}
      {deletingFolder && (
        <DeleteFolderDialog
          folder={deletingFolder}
          onDeleted={handleFolderDeleted}
          onClose={() => setDeletingFolder(null)}
        />
      )}
    </>
  );

  const folderSidebar = (
    <FolderSidebar
      folders={folders}
      currentFolder={currentFolder}
      onSelect={(next) => {
        setCurrentFolder(next);
        clearSelection();
      }}
      onCreate={() => setCreatingFolder(true)}
      onRename={setRenamingFolder}
      onDelete={setDeletingFolder}
    />
  );

  if (documents === null) {
    return warning || <LoadingRows />;
  }

  const inFolder = documents.filter((doc) =>
    inSelectedFolder(doc, currentFolder)
  );
  const needle = query.trim().toLocaleLowerCase();
  const tagChoices = uniqueTags(documents);
  const activeTag = tagChoices.some((tag) => tag.id === tagFilter)
    ? tagFilter
    : '';
  const visible = inFolder.filter(
    (doc) =>
      (statusFilter === '' || doc.status === statusFilter) &&
      (activeTag === '' || documentHasTag(doc, activeTag)) &&
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
    <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
      {folderSidebar}
      <div className="min-w-0 flex-1 space-y-4">
        {status}
        {warning}
        {folderWarning}
        {folderChrome}
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
          <div className="w-44 shrink-0">
            <Select
              id="tag-filter"
              label="Tag"
              hideLabel
              value={activeTag}
              onChange={(event) => setTagFilter(event.target.value)}
            >
              <option value="">All tags</option>
              {tagChoices.map((tag) => (
                <option key={tag.id} value={tag.id}>
                  {tag.name}
                </option>
              ))}
            </Select>
          </div>
          <p aria-live="polite" className="text-sm text-slate-500">
            {needle || statusFilter !== '' || activeTag !== ''
              ? `${visible.length} of ${inFolder.length} documents`
              : `${inFolder.length} ${inFolder.length === 1 ? 'document' : 'documents'}`}
          </p>
        </div>

        {documents.length === 0 ? (
          <EmptyLibrary />
        ) : inFolder.length === 0 ? (
          <EmptyFolder selection={currentFolder} folders={folders} />
        ) : visible.length === 0 ? (
          <NoMatches
            query={query.trim()}
            statusLabel={statusFilter ? STATUS_LABELS[statusFilter] : null}
            tagLabel={
              activeTag
                ? (tagChoices.find((tag) => tag.id === activeTag)?.name ?? null)
                : null
            }
            onClear={() => {
              setQuery('');
              setStatusFilter('');
              setTagFilter('');
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
              <caption className="sr-only">
                Your documents, newest first
              </caption>
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
                    availableTags={tagChoices}
                    selected={selected.has(doc.id)}
                    folders={folders}
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
  availableTags,
  selected,
  folders,
  onToggle,
  onChanged,
  onStale,
  onDeleted,
}: {
  doc: DocumentSummary;
  availableTags: DocumentTag[];
  selected: boolean;
  folders: LibraryFolder[];
  onToggle: (event: ReactMouseEvent) => void;
  onChanged: (doc: DocumentSummary) => void;

  // callback when the server indicates that the row is out of date
  onStale: () => void;
  onDeleted: (doc: DocumentSummary) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [addingTag, setAddingTag] = useState(false);
  const [tagError, setTagError] = useState<string | null>(null);
  const [moving, setMoving] = useState(false);
  const nameLink = useRef<HTMLAnchorElement>(null);
  const { cancel, canceling, cancelError } = useCancelProcessing(
    doc,
    onChanged
  );
  const reprocess = useReprocessAction(doc, onChanged, onStale);
  const interrupted = isInterrupted(doc);

  // inline reprocess link, shown only for incomplete documents
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

  async function handleRemoveTag(tag: DocumentTag) {
    setTagError(null);
    try {
      await removeDocumentTag(doc.id, tag.id);
      onChanged({
        ...doc,
        tags: (doc.tags ?? []).filter((item) => item.id !== tag.id),
      });
    } catch (err) {
      setTagError(toApiError(err).message);
    }
  }

  const actions: ActionMenuItem[] = [
    {
      label: 'Rename',
      icon: Pencil,
      keepFocus: true,
      onSelect: () => setEditing(true),
    },
    {
      label: 'Add tag',
      icon: TagIcon,
      keepFocus: true,
      onSelect: () => setAddingTag(true),
    },
    {
      label: 'Move to folder',
      icon: FolderInput,
      keepFocus: true,
      onSelect: () => setMoving(true),
    },
    // resumes unfinished work, or processes a finished document again
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
  // only until it finishes: a completed or failed job can't be canceled.
  if (canCancel(doc)) {
    actions.push({
      label: 'Cancel processing',
      icon: XCircle,
      destructive: true,
      onSelect: cancel,
    });
  }
  // last, as the most drastic. the dialog takes focus, and returns it to the
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
                {/* the uploaded and pages columns are hidden on small screens. */}
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
              <DocumentTagChips
                filename={doc.filename}
                tags={doc.tags ?? []}
                onRemove={handleRemoveTag}
              />
            </div>
          </div>
        )}
        {interrupted ? (
          <InterruptedNote>{reprocessLink}</InterruptedNote>
        ) : (
          <FailureNote doc={doc}>{reprocessLink}</FailureNote>
        )}
        {(cancelError || reprocess.error || tagError) && (
          <p role="alert" className="mt-0.5 text-xs text-red-700">
            {reprocess.error
              ? `Couldn't reprocess: ${reprocess.error}`
              : (cancelError ?? tagError)}
          </p>
        )}
        {reprocess.dialog}
        <AddTagDialog
          key={addingTag ? doc.id : 'closed'}
          doc={doc}
          availableTags={availableTags}
          open={addingTag}
          onClose={() => setAddingTag(false)}
          onAdded={(updated) => {
            setAddingTag(false);
            setTagError(null);
            onChanged(updated);
          }}
        />
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
        {moving && (
          <MoveToFolderDialog
            doc={doc}
            folders={folders}
            onMoved={(moved) => {
              setMoving(false);
              onChanged(moved);
            }}
            onClose={() => setMoving(false)}
          />
        )}
      </td>
    </tr>
  );
}

/** a checkbox that selects or clears every row in the current view. */
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
 * shown in place of the usual header area while at least one row is selected.
 * it replaces the "upload documents" action with bulk delete and reprocess.
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

/** confirms a bulk delete or reprocess before the request is sent. */
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
      } will be moved to Trash. You can restore them for 7 days.`
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
  tagLabel,
  onClear,
}: {
  query: string;
  statusLabel: string | null;
  tagLabel: string | null;
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
        ) : statusLabel ? (
          <>No documents with status {statusLabel}.</>
        ) : (
          <>No documents with tag {tagLabel}.</>
        )}
      </p>
      <Button variant="link" className="mt-2 text-sm" onClick={onClear}>
        Clear filters
      </Button>
    </div>
  );
}

function DocumentTagChips({
  filename,
  tags,
  onRemove,
}: {
  filename: string;
  tags: DocumentTag[];
  onRemove: (tag: DocumentTag) => void;
}) {
  if (tags.length === 0) return null;
  return (
    <ul className="mt-1.5 flex flex-wrap gap-1">
      {tags.map((tag) => (
        <li key={tag.id}>
          <span className="inline-flex items-center gap-0.5 rounded-full border border-slate-200 bg-slate-50 py-0.5 pl-2 pr-0.5 text-xs font-medium text-slate-700">
            {tag.name}
            <button
              type="button"
              aria-label={`Remove tag ${tag.name} from ${filename}`}
              onClick={() => onRemove(tag)}
              className="rounded-full p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
            >
              <X aria-hidden="true" className="size-3" />
            </button>
          </span>
        </li>
      ))}
    </ul>
  );
}

function AddTagDialog({
  doc,
  availableTags,
  open,
  onClose,
  onAdded,
}: {
  doc: DocumentSummary;
  availableTags: DocumentTag[];
  open: boolean;
  onClose: () => void;
  onAdded: (doc: DocumentSummary) => void;
}) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const unused = availableTags.filter((tag) => !documentHasTag(doc, tag.id));

  async function save(value: string) {
    const problem = tagNameError(value);
    if (problem) return setError(problem);
    setBusy(true);
    setError(null);
    try {
      onAdded(await addDocumentTag(doc.id, value.trim()));
      setName('');
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissible={!busy}
      title={`Add a tag to ${doc.filename}`}
      footer={
        <>
          <Button
            variant="secondary"
            className="sm:w-auto"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            className="sm:w-auto"
            onClick={() => save(name)}
            loading={busy}
          >
            Add tag
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          save(name);
        }}
      >
        <Input
          id={`add-tag-${doc.id}`}
          label="Tag name"
          value={name}
          maxLength={MAX_TAG_NAME_LENGTH}
          onChange={(event) => setName(event.target.value)}
          error={error ?? undefined}
          data-autofocus
        />
      </form>
      {unused.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium text-slate-500">Your tags</p>
          <ul className="mt-1.5 flex flex-wrap gap-1">
            {unused.map((tag) => (
              <li key={tag.id}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => save(tag.name)}
                  className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-xs font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:text-slate-400"
                >
                  {tag.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Modal>
  );
}

function folderNavClass(active: boolean): string {
  return cn(
    'flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm font-medium',
    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900',
    active
      ? 'bg-slate-900 text-white'
      : 'text-slate-700 hover:bg-slate-100 hover:text-slate-900'
  );
}

function FolderSidebar({
  folders,
  currentFolder,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: {
  folders: LibraryFolder[];
  currentFolder: FolderSelection;
  onSelect: (next: FolderSelection) => void;
  onCreate: () => void;
  onRename: (folder: LibraryFolder) => void;
  onDelete: (folder: LibraryFolder) => void;
}) {
  return (
    <nav
      aria-label="Folders"
      className="w-full shrink-0 rounded-xl border border-slate-200 bg-white p-2 lg:w-56"
    >
      <p className="px-2.5 pb-1 pt-1 text-xs font-medium uppercase tracking-wide text-slate-500">
        Folders
      </p>
      <ul className="space-y-0.5">
        <li>
          <button
            type="button"
            aria-current={currentFolder === 'all' ? 'page' : undefined}
            className={folderNavClass(currentFolder === 'all')}
            onClick={() => onSelect('all')}
          >
            <FolderOpen aria-hidden="true" className="size-4 shrink-0" />
            All documents
          </button>
        </li>
        <li>
          <button
            type="button"
            aria-current={currentFolder === 'unfiled' ? 'page' : undefined}
            className={folderNavClass(currentFolder === 'unfiled')}
            onClick={() => onSelect('unfiled')}
          >
            <FileText aria-hidden="true" className="size-4 shrink-0" />
            Unfiled
          </button>
        </li>
        {folders.map((folder) => {
          const active = currentFolder === folder.id;
          return (
            <li key={folder.id} className="flex items-center gap-0.5">
              <button
                type="button"
                aria-current={active ? 'page' : undefined}
                className={cn(folderNavClass(active), 'min-w-0 flex-1')}
                onClick={() => onSelect(folder.id)}
              >
                <Folder aria-hidden="true" className="size-4 shrink-0" />
                <span className="truncate">{folder.name}</span>
              </button>
              <ActionMenu
                label={`Folder actions for ${folder.name}`}
                items={[
                  {
                    label: 'Rename',
                    icon: Pencil,
                    keepFocus: true,
                    onSelect: () => onRename(folder),
                  },
                  {
                    label: 'Delete',
                    icon: Trash,
                    destructive: true,
                    keepFocus: true,
                    onSelect: () => onDelete(folder),
                  },
                ]}
              />
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        onClick={onCreate}
        className="mt-2 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
      >
        <FolderPlus aria-hidden="true" className="size-4 shrink-0" />
        New folder
      </button>
    </nav>
  );
}

function EmptyFolder({
  selection,
  folders,
}: {
  selection: FolderSelection;
  folders: LibraryFolder[];
}) {
  const name =
    selection === 'unfiled'
      ? 'Unfiled'
      : (folders.find((folder) => folder.id === selection)?.name ??
        'this folder');
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center">
      <span className="flex size-11 items-center justify-center rounded-full bg-slate-100 text-slate-500">
        <Folder aria-hidden="true" className="size-5" />
      </span>
      <h2 className="mt-4 text-base font-semibold text-slate-900">
        {selection === 'unfiled' ? 'No unfiled documents' : `${name} is empty`}
      </h2>
      <p className="mt-1 max-w-sm text-sm text-slate-600">
        {selection === 'unfiled'
          ? 'Documents you have not moved into a folder will show up here.'
          : 'Move a document into this folder from its action menu.'}
      </p>
    </div>
  );
}

function FolderNameDialog({
  title,
  confirmLabel,
  initialName = '',
  onSave,
  onSaved,
  onClose,
}: {
  title: string;
  confirmLabel: string;
  initialName?: string;
  onSave: (name: string) => Promise<LibraryFolder>;
  onSaved: (folder: LibraryFolder) => void;
  onClose: () => void;
}) {
  const inputId = useId();
  const [name, setName] = useState(initialName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed) {
      setError('Name cannot be empty.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onSaved(await onSave(trimmed));
    } catch (err) {
      const apiError = toApiError(err);
      setError(apiError.fields.name ?? apiError.message);
    } finally {
      setBusy(false);
    }
  }

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
          >
            Cancel
          </Button>
          <Button className="sm:w-auto" onClick={submit} loading={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <Input
        id={inputId}
        label="Folder name"
        value={name}
        maxLength={255}
        autoFocus
        data-autofocus
        disabled={busy}
        error={error ?? undefined}
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            submit();
          }
        }}
      />
    </Modal>
  );
}

function DeleteFolderDialog({
  folder,
  onDeleted,
  onClose,
}: {
  folder: LibraryFolder;
  onDeleted: (folder: LibraryFolder) => void;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await deleteFolder(folder.id);
      onDeleted(folder);
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      title={`Delete ${folder.name}?`}
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
            variant="danger"
            className="sm:w-auto"
            onClick={confirm}
            loading={busy}
          >
            {error ? 'Try again' : 'Delete'}
          </Button>
        </>
      }
    >
      <p>
        Documents in this folder will become unfiled. They will not be deleted.
      </p>
      {error && (
        <Alert className="mt-4" tone="error">
          {error}
        </Alert>
      )}
    </Modal>
  );
}

function MoveToFolderDialog({
  doc,
  folders,
  onMoved,
  onClose,
}: {
  doc: DocumentSummary;
  folders: LibraryFolder[];
  onMoved: (doc: DocumentSummary) => void;
  onClose: () => void;
}) {
  const [target, setTarget] = useState<string>(doc.folder_id ?? 'unfiled');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    const folderId = target === 'unfiled' ? null : target;
    if (folderId === (doc.folder_id ?? null)) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onMoved(await moveDocumentToFolder(doc.id, folderId));
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      title={`Move ${doc.filename}`}
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
          <Button className="sm:w-auto" onClick={confirm} loading={busy}>
            {error ? 'Try again' : 'Move'}
          </Button>
        </>
      }
    >
      <fieldset className="space-y-1.5" disabled={busy}>
        <legend className="sr-only">Choose a folder</legend>
        <label className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-slate-800 hover:bg-slate-50">
          <input
            type="radio"
            name="move-folder"
            checked={target === 'unfiled'}
            onChange={() => setTarget('unfiled')}
            className="border-slate-300 text-slate-900 focus:ring-slate-900"
          />
          Unfiled
        </label>
        {folders.map((folder) => (
          <label
            key={folder.id}
            className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-slate-800 hover:bg-slate-50"
          >
            <input
              type="radio"
              name="move-folder"
              checked={target === folder.id}
              onChange={() => setTarget(folder.id)}
              className="border-slate-300 text-slate-900 focus:ring-slate-900"
            />
            {folder.name}
          </label>
        ))}
        {folders.length === 0 && (
          <p className="px-2 text-sm text-slate-500">
            Create a folder first to file this document.
          </p>
        )}
      </fieldset>
      {error && (
        <Alert className="mt-4" tone="error">
          {error}
        </Alert>
      )}
    </Modal>
  );
}
