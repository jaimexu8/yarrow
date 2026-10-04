'use client';

import { useId, useState, type ReactNode } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Spinner } from '@/components/ui/Spinner';
import {
  cancelProcessing,
  renameDocument,
  reprocessDocument,
  deleteDocument,
  type DocumentSummary,
} from '@/lib/documents';
import { cn } from '@/lib/cn';
import { toApiError } from '@/lib/errors';

/** Mirrors the server's rules, so most mistakes are caught before sending. */
export function checkName(name: string): string | null {
  if (!name) return 'Name cannot be empty.';
  if (name.length > 255) return 'Name must be 255 characters or fewer.';
  // eslint-disable-next-line no-control-regex
  if (/[\\/\u0000-\u001f\u007f]/.test(name)) {
    return 'Name cannot contain slashes or control characters.';
  }
  return null;
}

/**
 * Inline rename form. Saving an unchanged name just closes the form. On
 * failure, the old name stays and the reason is shown.
 */
export function RenameForm({
  doc,
  onSaved,
  onDone,
}: {
  doc: DocumentSummary;
  onSaved: (doc: DocumentSummary) => void;
  onDone: () => void;
}) {
  const inputId = useId();
  const errorId = useId();
  const [draft, setDraft] = useState(doc.filename);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const name = draft.trim();
    if (name === doc.filename) return onDone();
    const problem = checkName(name);
    if (problem) return setError(problem);
    setSaving(true);
    try {
      onSaved(await renameDocument(doc.id, name));
      onDone();
    } catch (err) {
      const apiError = toApiError(err);
      setError(apiError.fields.filename ?? apiError.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="space-y-1"
      onSubmit={(event) => {
        event.preventDefault();
        save();
      }}
    >
      <label htmlFor={inputId} className="sr-only">
        New name for {doc.filename}
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <input
          id={inputId}
          value={draft}
          maxLength={255}
          autoFocus
          disabled={saving}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(event) => setDraft(event.target.value)}
          onFocus={(event) => {
            // Select the name but not the extension, so typing replaces
            // "report" in "report.pdf".
            const dot = event.target.value.lastIndexOf('.');
            event.target.setSelectionRange(
              0,
              dot > 0 ? dot : event.target.value.length
            );
          }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') onDone();
          }}
          className={cn(
            'min-w-0 flex-1 rounded-lg border px-2 py-1 text-sm text-slate-900',
            error ? 'border-red-600' : 'border-slate-300'
          )}
        />
        <Button type="submit" className="w-auto px-3" loading={saving}>
          Save
        </Button>
        <Button
          type="button"
          variant="secondary"
          className="w-auto px-3"
          disabled={saving}
          onClick={onDone}
        >
          Cancel
        </Button>
      </div>
      {error && (
        <p id={errorId} role="alert" className="text-xs text-red-700">
          {error}
        </p>
      )}
    </form>
  );
}

/**
 * Cancel a queued document's processing. Fails once a worker has
 * started it.
 */
export function useCancelProcessing(
  doc: DocumentSummary,
  onChanged: (doc: DocumentSummary) => void
) {
  const [canceling, setCanceling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  async function cancel() {
    setCanceling(true);
    setCancelError(null);
    try {
      onChanged(await cancelProcessing(doc.id));
    } catch (err) {
      setCancelError(toApiError(err).message);
    } finally {
      setCanceling(false);
    }
  }

  return { cancel, canceling, cancelError };
}

/**
 * Reprocess a finished, failed, or canceled document. The server refuses
 * (409) once a job is queued or running.
 */
export function useReprocessDocument(
  doc: DocumentSummary,
  onChanged: (doc: DocumentSummary) => void
) {
  const [reprocessing, setReprocessing] = useState(false);
  const [reprocessError, setReprocessError] = useState<string | null>(null);

  async function reprocess() {
    setReprocessing(true);
    setReprocessError(null);
    try {
      onChanged(await reprocessDocument(doc.id));
    } catch (err) {
      setReprocessError(toApiError(err).message);
    } finally {
      setReprocessing(false);
    }
  }

  return { reprocess, reprocessing, reprocessError };
}

/**
 * Why processing failed. The server only sends reasons written for
 * users. A completed document can also carry a note when some of its pages
 * failed. It stays "Completed" and the note is shown as a warning, not an
 * error.
 */
export function FailureNote({
  doc,
  children,
}: {
  doc: DocumentSummary;
  children?: ReactNode;
}) {
  if (!doc.error_message) return null;
  const failed = doc.status === 'failed';
  return (
    <p
      className={cn(
        'mt-0.5 text-xs',
        failed ? 'text-red-700' : 'text-amber-800'
      )}
    >
      {!failed && 'Some pages could not be read: '}
      {doc.error_message}
      {children}
    </p>
  );
}

/** A document whose job was lost mid-run. Nothing is wrong with
 * the file, so this is a neutral note rather than an error. */
export function InterruptedNote({ children }: { children?: ReactNode }) {
  return (
    <p className="mt-0.5 text-xs text-slate-600">
      Processing stopped before it finished. Finished pages are kept.
      {children}
    </p>
  );
}

/**
 * Reprocess a document. It only processes the pages that are not
 * completed, so nothing is lost. A finished document is processed again in
 * full, replacing its results, so that asks for confirmation first
 */
export function useReprocessAction(
  doc: DocumentSummary,
  onChanged: (doc: DocumentSummary) => void,
  onStale?: () => void
) {
  const [reprocessing, setReprocessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  async function run() {
    setReprocessing(true);
    setError(null);
    try {
      onChanged(await reprocessDocument(doc.id));
      setConfirming(false);
    } catch (err) {
      const apiError = toApiError(err);
      setError(apiError.message);
      if (apiError.status === 409) onStale?.();
    } finally {
      setReprocessing(false);
    }
  }

  function request() {
    setError(null);
    if (doc.reprocess?.scope === 'all') setConfirming(true);
    else run();
  }

  const dialog = (
    <ConfirmReprocessDialog
      doc={doc}
      open={confirming}
      reprocessing={reprocessing}
      error={error}
      onConfirm={run}
      onClose={() => {
        setConfirming(false);
        setError(null);
      }}
    />
  );

  return {
    // Whether reprocessing is possible now (no job is running)
    available: Boolean(doc.reprocess),
    request,
    reprocessing,

    // A failure of an immediate (unconfirmed) reprocess
    error: confirming ? null : error,
    dialog,
  };
}

function ConfirmReprocessDialog({
  doc,
  open,
  reprocessing,
  error,
  onConfirm,
  onClose,
}: {
  doc: DocumentSummary;
  open: boolean;
  reprocessing: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const pages = doc.reprocess?.pages;
  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissible={!reprocessing}
      title="Reprocess document?"
      footer={
        <>
          <Button
            variant="secondary"
            className="sm:w-auto"
            onClick={onClose}
            disabled={reprocessing}
            data-autofocus
          >
            Cancel
          </Button>
          <Button
            className="sm:w-auto"
            onClick={onConfirm}
            loading={reprocessing}
          >
            {error ? 'Try again' : 'Reprocess'}
          </Button>
        </>
      }
    >
      <p className="break-words">
        {pages
          ? `All ${pages} ${pages === 1 ? 'page' : 'pages'} of `
          : 'Every page of '}
        <span className="font-medium text-slate-900">{doc.filename}</span> will
        be processed again. The current results stay available until it
        finishes, then are replaced, including any table merges or splits you
        made.
      </p>
      {error && (
        <Alert className="mt-4" tone="error">
          {error}
        </Alert>
      )}
    </Modal>
  );
}

/** The inline "· Reprocess" link that follows a failure or interrupted note. */
export function ReprocessLink({
  reprocessing,
  onClick,
}: {
  reprocessing: boolean;
  onClick: () => void;
}) {
  return (
    <>
      {' · '}
      {reprocessing ? (
        <span className="inline-flex items-center gap-1 text-slate-600">
          <Spinner label={null} className="size-3" />
          Starting…
        </span>
      ) : (
        <button
          type="button"
          onClick={onClick}
          className="font-medium text-slate-900 underline underline-offset-2 hover:text-slate-600 focus-visible:rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          Reprocess
        </button>
      )}
    </>
  );
}

/**
 * Confirm dialog before permanently deleting a document. While the
 * request is in flight the dialog cannot be dismissed, and a failure is shown
 * inside it so the user can retry or back out.
 */
export function DeleteDocumentDialog({
  doc,
  open,
  onClose,
  onDeleted,
}: {
  doc: DocumentSummary;
  open: boolean;
  onClose: () => void;
  /** Called once the document is gone from the server. */
  onDeleted: (doc: DocumentSummary) => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inProgress = doc.status === 'queued' || doc.status === 'processing';

  function close() {
    setError(null);
    onClose();
  }

  async function confirm() {
    setDeleting(true);
    setError(null);
    try {
      await deleteDocument(doc.id);
      onDeleted(doc);
    } catch (err) {
      const apiError = toApiError(err);
      if (apiError.status === 404) {
        // Already deleted, e.g. from another tab: what the user asked for.
        onDeleted(doc);
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
      title="Delete document?"
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
            {error ? 'Try again' : 'Delete'}
          </Button>
        </>
      }
    >
      <p className="break-words">
        <span className="font-medium text-slate-900">{doc.filename}</span> and
        everything extracted from it will be permanently deleted.
        {inProgress && ' Its processing will be stopped.'} This can&apos;t be
        undone.
      </p>
      {error && (
        <Alert className="mt-4" tone="error">
          {error}
        </Alert>
      )}
    </Modal>
  );
}
