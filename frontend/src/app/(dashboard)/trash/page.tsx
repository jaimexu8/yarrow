'use client';

import { useEffect, useState } from 'react';
import { FileText, RotateCcw, Trash, Trash2 } from 'lucide-react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Spinner } from '@/components/ui/Spinner';
import { cn } from '@/lib/cn';
import {
  TRASH_RETENTION_DAYS,
  emptyTrash,
  listTrash,
  parseApiDate,
  permanentlyDeleteDocument,
  restoreDocument,
  trashDaysLeft,
  type DocumentSummary,
} from '@/lib/documents';
import { toApiError } from '@/lib/errors';
import { formatBytes } from '@/lib/uploads';

const CELL =
  'border-y border-slate-200 bg-white px-3 py-3 sm:px-4 align-middle first:rounded-l-xl first:border-l last:rounded-r-xl last:border-r';
const HEAD = 'px-3 pb-1 text-left sm:px-4 text-xs font-medium text-slate-500';

export default function TrashPage() {
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [emptying, setEmptying] = useState(false);
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  const [emptyError, setEmptyError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    listTrash()
      .then((docs) => {
        if (!current) return;
        setDocuments(docs);
        setError(null);
      })
      .catch((err) => {
        if (!current) return;
        setError(toApiError(err).message);
      });
    return () => {
      current = false;
    };
  }, []);

  function drop(id: string) {
    setDocuments((current) => (current ?? []).filter((doc) => doc.id !== id));
  }

  async function onEmpty() {
    setEmptying(true);
    setEmptyError(null);
    try {
      await emptyTrash();
      setDocuments([]);
      setConfirmEmpty(false);
      setAnnouncement('Trash emptied.');
    } catch (err) {
      setEmptyError(toApiError(err).message);
    } finally {
      setEmptying(false);
    }
  }

  const count = documents?.length ?? 0;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Trash
          </h1>
          <p className="text-sm text-slate-600">
            Deleted documents stay here for {TRASH_RETENTION_DAYS} days, then
            are permanently deleted.
          </p>
        </div>
        {count > 0 && (
          <Button
            variant="danger"
            className="w-auto"
            onClick={() => {
              setEmptyError(null);
              setConfirmEmpty(true);
            }}
          >
            <Trash aria-hidden="true" className="size-4" />
            Empty trash
          </Button>
        )}
      </div>

      <p role="status" className="sr-only">
        {announcement}
      </p>

      {error && <Alert tone="error">Couldn&apos;t load trash ({error})</Alert>}

      {documents === null && !error ? (
        <LoadingRows />
      ) : documents && documents.length === 0 ? (
        <EmptyTrash />
      ) : documents ? (
        <table className="w-full border-separate border-spacing-y-2">
          <caption className="sr-only">Documents in trash</caption>
          <thead>
            <tr>
              <th scope="col" className={HEAD}>
                Document
              </th>
              <th scope="col" className={cn(HEAD, 'hidden sm:table-cell')}>
                Deleted
              </th>
              <th scope="col" className={HEAD}>
                Time left
              </th>
              <th scope="col" className={cn(HEAD, 'text-right')}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <TrashRow
                key={doc.id}
                doc={doc}
                onRestored={() => {
                  drop(doc.id);
                  setAnnouncement(`Restored ${doc.filename}.`);
                }}
                onDeleted={() => {
                  drop(doc.id);
                  setAnnouncement(`Permanently deleted ${doc.filename}.`);
                }}
              />
            ))}
          </tbody>
        </table>
      ) : null}

      {confirmEmpty && (
        <Modal
          open
          onClose={() => setConfirmEmpty(false)}
          dismissible={!emptying}
          title="Empty trash?"
          footer={
            <>
              <Button
                variant="secondary"
                className="sm:w-auto"
                onClick={() => setConfirmEmpty(false)}
                disabled={emptying}
                data-autofocus
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                className="sm:w-auto"
                onClick={onEmpty}
                loading={emptying}
              >
                {emptyError ? 'Try again' : 'Empty trash'}
              </Button>
            </>
          }
        >
          <p>
            Every document in trash and everything extracted from them will be
            permanently deleted. This can&apos;t be undone.
          </p>
          {emptyError && (
            <Alert className="mt-4" tone="error">
              {emptyError}
            </Alert>
          )}
        </Modal>
      )}
    </div>
  );
}

function TrashRow({
  doc,
  onRestored,
  onDeleted,
}: {
  doc: DocumentSummary;
  onRestored: () => void;
  onDeleted: () => void;
}) {
  const [restoring, setRestoring] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deletedAt = doc.deleted_at ?? doc.created_at;

  async function restore() {
    setRestoring(true);
    setRestoreError(null);
    try {
      await restoreDocument(doc.id);
      onRestored();
    } catch (err) {
      const apiError = toApiError(err);
      if (apiError.status === 404 || apiError.status === 410) {
        onDeleted();
        return;
      }
      setRestoreError(apiError.message);
    } finally {
      setRestoring(false);
    }
  }

  return (
    <tr>
      <td className={cn(CELL, 'w-full max-w-0')}>
        <div className="flex min-w-0 items-center gap-3">
          <FileText
            aria-hidden="true"
            className="hidden size-5 shrink-0 text-slate-400 sm:block"
          />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-slate-900">
              {doc.filename}
            </p>
            <p className="truncate text-xs text-slate-500">
              {formatBytes(doc.file_size_bytes)}
              {deletedAt && (
                <span className="sm:hidden">
                  {' · '}
                  {parseApiDate(deletedAt).toLocaleDateString(undefined, {
                    dateStyle: 'medium',
                  })}
                </span>
              )}
            </p>
          </div>
        </div>
        {restoreError && (
          <p role="alert" className="mt-0.5 text-xs text-red-700">
            {restoreError}
          </p>
        )}
      </td>
      <td className={cn(CELL, 'hidden whitespace-nowrap sm:table-cell')}>
        {deletedAt ? (
          <time dateTime={parseApiDate(deletedAt).toISOString()}>
            {parseApiDate(deletedAt).toLocaleDateString(undefined, {
              dateStyle: 'medium',
            })}
          </time>
        ) : (
          <span className="text-slate-400">—</span>
        )}
      </td>
      <td className={cn(CELL, 'whitespace-nowrap text-sm text-slate-700')}>
        {deletedAt ? daysLeftLabel(deletedAt) : '—'}
      </td>
      <td className={cn(CELL, 'whitespace-nowrap')}>
        <div className="flex items-center justify-end gap-1">
          <Button
            variant="secondary"
            className="w-auto px-3"
            onClick={restore}
            loading={restoring}
          >
            <RotateCcw aria-hidden="true" className="size-4" />
            Restore
          </Button>
          <Button
            variant="danger"
            className="w-auto px-3"
            onClick={() => setConfirmingDelete(true)}
          >
            <Trash aria-hidden="true" className="size-4" />
            Delete forever
          </Button>
        </div>
        <PermanentDeleteDialog
          doc={doc}
          open={confirmingDelete}
          onClose={() => setConfirmingDelete(false)}
          onDeleted={onDeleted}
        />
      </td>
    </tr>
  );
}

function PermanentDeleteDialog({
  doc,
  open,
  onClose,
  onDeleted,
}: {
  doc: DocumentSummary;
  open: boolean;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close() {
    setError(null);
    onClose();
  }

  async function confirm() {
    setDeleting(true);
    setError(null);
    try {
      await permanentlyDeleteDocument(doc.id);
      onDeleted();
    } catch (err) {
      const apiError = toApiError(err);
      if (apiError.status === 404) {
        onDeleted();
        return;
      }
      setError(apiError.message);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={close}
      dismissible={!deleting}
      title="Delete forever?"
      footer={
        <>
          <Button
            variant="secondary"
            className="sm:w-auto"
            onClick={close}
            disabled={deleting}
            data-autofocus
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            className="sm:w-auto"
            onClick={confirm}
            loading={deleting}
          >
            {error ? 'Try again' : 'Delete forever'}
          </Button>
        </>
      }
    >
      <p className="break-words">
        <span className="font-medium text-slate-900">{doc.filename}</span> and
        everything extracted from it will be permanently deleted. This
        can&apos;t be undone.
      </p>
      {error && (
        <Alert className="mt-4" tone="error">
          {error}
        </Alert>
      )}
    </Modal>
  );
}

function daysLeftLabel(deletedAt: string): string {
  const days = trashDaysLeft(deletedAt);
  if (days <= 0) return 'Expires today';
  if (days === 1) return '1 day left';
  return `${days} days left`;
}

function LoadingRows() {
  return (
    <div aria-busy="true" className="space-y-2">
      <span className="sr-only">
        <Spinner label="Loading trash" />
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
        </div>
      ))}
    </div>
  );
}

function EmptyTrash() {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center">
      <span className="flex size-11 items-center justify-center rounded-full bg-slate-100 text-slate-500">
        <Trash2 aria-hidden="true" className="size-5" />
      </span>
      <h2 className="mt-4 text-base font-semibold text-slate-900">
        Trash is empty
      </h2>
      <p className="mt-1 max-w-sm text-sm text-slate-600">
        Documents you delete will show up here for {TRASH_RETENTION_DAYS} days.
      </p>
    </div>
  );
}
