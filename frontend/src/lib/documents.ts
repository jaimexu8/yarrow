import { isAxiosError } from 'axios';
import api from './api';
import { toApiError } from './errors';
import { notifyStorageChanged } from './storage';

/** Matches Document.status on the backend. */
export type DocumentStatus =
  'queued' | 'processing' | 'completed' | 'failed' | 'canceled';

/** Human-readable label for each processing status (NFR-13). */
export const STATUS_LABELS: Record<DocumentStatus, string> = {
  queued: 'Queued',
  processing: 'Processing',
  completed: 'Completed',
  failed: 'Failed',
  canceled: 'Canceled',
};

export type ReprocessInfo = {
  // some pages to be processed (incomplete) or all of them (all)
  scope: 'incomplete' | 'all';

  // Its job was lost mid-run (e.g. a worker crashed)
  interrupted: boolean;

  // Pages that would be processed, or null for all of them
  pages: number | null;

  // The first few unfinished page numbers, for display ("incomplete" only).
  page_numbers: number[];
};

export type DocumentTag = {
  id: string;
  name: string;
};

export type ModelUsage = {
  model_name: string | null;
  model_version: string | null;
  page_count: number;
};

export type DocumentSummary = {
  id: string;
  filename: string;
  file_size_bytes: number;
  file_type: string;
  page_count: number | null;
  status: DocumentStatus | null;
  error_message: string | null;
  created_at: string | null;
  deleted_at?: string | null;
  folder_id: string | null;
  reprocess?: ReprocessInfo | null;
  tags?: DocumentTag[];
  model_name?: string | null;
  model_version?: string | null;
  pipeline_version?: string | null;
  processed_at?: string | null;
};

export type DocumentDetail = DocumentSummary & {
  models: ModelUsage[];
};

/** Keep in sync with backend TRASH_RETENTION_DAYS. Not a user setting. */
export const TRASH_RETENTION_DAYS = 7;
const TRASH_RETENTION_MS = TRASH_RETENTION_DAYS * 24 * 60 * 60 * 1000;

export function isInterrupted(doc: DocumentSummary): boolean {
  return doc.reprocess?.interrupted ?? false;
}

/** Checks if a document is queued or processing, and not interrupted */
export function isWorkingOn(doc: DocumentSummary): boolean {
  return (
    (doc.status === 'queued' || doc.status === 'processing') &&
    !isInterrupted(doc)
  );
}

/**
 * Send request to reprocess a document without uploading it again. Unfinished
 * work (interrupted, failed or partly failed) reprocesses only the pages that
 * are not completed; a finished document is processed again in full
 */
export async function reprocessDocument(id: string): Promise<DocumentSummary> {
  const res = await api.post<DocumentSummary>(
    `/api/v1/documents/${id}/reprocess`
  );
  return res.data;
}

export async function getDocument(id: string): Promise<DocumentDetail> {
  const res = await api.get<DocumentDetail>(`/api/v1/documents/${id}`);
  return res.data;
}

/**
 * List documents the caller can see.
 *
 * Omit ``folderId`` for the full library (owned and shared). Pass ``null``
 * for owned unfiled documents, or a folder id for owned documents in that
 * folder.
 */
export async function listDocuments(
  folderId?: string | null,
  tagId?: string
): Promise<DocumentSummary[]> {
  const filters: Record<string, string> = {};
  if (folderId !== undefined) {
    filters.folder_id = folderId === null ? 'null' : folderId;
  }
  if (tagId !== undefined) filters.tag_id = tagId;
  const params = Object.keys(filters).length > 0 ? filters : undefined;
  const res = await api.get<DocumentSummary[]>('/api/v1/documents/', {
    params,
  });
  return res.data;
}

/**
 * Move a document into a folder, or unfile it with null (US-103).
 */
export async function moveDocumentToFolder(
  id: string,
  folderId: string | null
): Promise<DocumentSummary> {
  const res = await api.patch<DocumentSummary>(
    `/api/v1/documents/${id}/folder`,
    { folder_id: folderId }
  );
  return res.data;
}

/**
 * Rename a document (US-39). Only the display name changes. Returns the
 * document as saved, since the server trims the name.
 */
export async function renameDocument(
  id: string,
  filename: string
): Promise<DocumentSummary> {
  const res = await api.patch<DocumentSummary>(`/api/v1/documents/${id}`, {
    filename,
  });
  return res.data;
}

/**
 * Whether the user can still cancel this document's processing (US-42):
 * while it is queued or being processed, not once it has finished.
 */
export function canCancel(doc: DocumentSummary): boolean {
  return doc.status === 'queued' || doc.status === 'processing';
}

/**
 * Cancel a document's processing while it is queued or processing (US-42).
 * The server refuses (409) once it has finished, so this can fail if the
 * list was a moment out of date.
 */
export async function cancelProcessing(id: string): Promise<DocumentSummary> {
  const res = await api.post<DocumentSummary>(`/api/v1/documents/${id}/cancel`);
  return res.data;
}

/** The API sends naive UTC timestamps; mark them as UTC before parsing. */
export function parseApiDate(value: string): Date {
  const utc = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`;
  return new Date(utc);
}

/**
 * A new id for one file's upload. The same id is sent with every attempt for
 * that file, so if a response is lost and the user retries, the server
 * returns the document it already stored instead of storing a duplicate.
 */
export function newUploadId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  // randomUUID only exists on HTTPS and localhost; fall back elsewhere.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * A new id for one upload run (US-31). Every file in the run is sent with it,
 * so the server can group them and report their progress together.
 */
export function newBatchId(): string {
  return newUploadId();
}

/** Per-status counts for one upload run; matches BatchProgress on the server. */
export type BatchProgress = {
  batch_id: string;
  total: number;
  queued: number;
  processing: number;
  completed: number;
  failed: number;
  canceled: number;
  created_at: string | null;
  /** When every file reached a final state (US-60); null while any is left. */
  completed_at?: string | null;
};

/** Whether any file in the batch is still queued or being processed. */
export function isBatchWorking(progress: BatchProgress): boolean {
  return progress.queued + progress.processing > 0;
}

/**
 * The latest counts for a batch (US-31). Counted on the server from the
 * documents' current statuses, so it is accurate after leaving and coming
 * back to the page. Someone else's batch, or an unknown one, is a 404.
 */
export async function getBatchProgress(
  batchId: string
): Promise<BatchProgress> {
  const res = await api.get<BatchProgress>(
    `/api/v1/documents/batches/${batchId}`
  );
  return res.data;
}

type UploadResponse = {
  accepted: {
    document_id: string;
    filename: string;
    status?: string;
    message?: string | null;
  }[];
  rejected: UploadRejection[];
};

/** The caller's own document an upload was identical to (US-79). */
type DuplicateOfResponse = {
  document_id: string;
  filename: string;
  status?: string | null;
};

type UploadRejection = {
  filename?: string;
  reason: string;
  retryable?: boolean;
  duplicate_of?: DuplicateOfResponse | null;
};

/** An existing document in the user's own library (US-79). */
export type DuplicateOf = {
  documentId: string;
  filename: string;
  status?: string;
};

/**
 * What happened to one file:
 * - accepted: stored. ``processingStarted`` is false when the file was kept
 *   but processing could not be started (``message`` says why).
 * - rejected: the server refused this file, with a reason (type, size, quota)
 * - failed: the upload itself went wrong (network, server error). Retrying
 *   with the same upload id is always safe.
 * - duplicate: nothing was stored because the user already has a document
 *   with identical contents (US-79). Sending it again with
 *   ``allowDuplicate`` keeps a second copy.
 */
export type UploadOutcome =
  | {
      kind: 'accepted';
      documentId: string;
      processingStarted: boolean;
      message?: string;
    }
  | { kind: 'rejected'; reason: string }
  | { kind: 'duplicate'; reason: string; duplicateOf: DuplicateOf }
  | { kind: 'failed'; message: string };

/**
 * Upload a single file (US-7). The page sends files one at a time so each has
 * its own progress and result, and one failure can't affect the rest (US-8).
 * ``batchId`` ties the file to its upload run (US-31). ``allowDuplicate``
 * stores the file even if the user already has an identical one (US-79,
 * "Upload anyway"); without it such a file comes back as ``duplicate``.
 */
export async function uploadDocument(
  file: File,
  uploadId: string,
  onProgress: (percent: number) => void,
  batchId?: string,
  allowDuplicate = false
): Promise<UploadOutcome> {
  const form = new FormData();
  form.append('files', file);
  form.append('client_upload_ids', uploadId);
  // Groups this file with the others of the same run (US-31).
  if (batchId) form.append('batch_id', batchId);
  if (allowDuplicate) form.append('allow_duplicate', 'true');
  try {
    const res = await api.post<UploadResponse>(
      '/api/v1/documents/upload',
      form,
      {
        onUploadProgress: (event) => {
          const total = event.total || file.size || 1;
          onProgress(Math.min(100, Math.round((event.loaded / total) * 100)));
        },
      }
    );
    const accepted = res.data.accepted[0];
    if (accepted) {
      notifyStorageChanged();
      return {
        kind: 'accepted',
        documentId: accepted.document_id,
        processingStarted: accepted.status !== 'failed',
        message: accepted.message ?? undefined,
      };
    }
    return fromRejection(res.data.rejected[0]);
  } catch (err) {
    // A 400 carries the per-file rejection reasons.
    if (isAxiosError(err) && err.response?.status === 400) {
      const detail = (err.response.data as { detail?: unknown })?.detail;
      if (Array.isArray(detail) && typeof detail[0]?.reason === 'string') {
        return fromRejection(detail[0]);
      }
    }
    return { kind: 'failed', message: toApiError(err).message };
  }
}

/**
 * Move a document to trash. It can be restored for 7 days. Throws the raw
 * request error like the other calls here; pass it to toApiError for
 * display. A 409 (code DOCUMENT_BUSY) means processing was saving results
 * at that moment and the delete can be retried.
 */
export async function deleteDocument(id: string): Promise<void> {
  await api.delete(`/api/v1/documents/${id}`);
  notifyStorageChanged();
}

export async function listTrash(): Promise<DocumentSummary[]> {
  const res = await api.get<DocumentSummary[]>('/api/v1/documents/trash');
  return res.data;
}

export async function restoreDocument(id: string): Promise<DocumentSummary> {
  const res = await api.post<DocumentSummary>(
    `/api/v1/documents/${id}/restore`
  );
  notifyStorageChanged();
  return res.data;
}

/** Permanently wipe a trashed document without waiting out the 7 days. */
export async function permanentlyDeleteDocument(id: string): Promise<void> {
  await api.delete(`/api/v1/documents/${id}/permanent`);
  notifyStorageChanged();
}

export async function emptyTrash(): Promise<void> {
  await api.delete('/api/v1/documents/trash');
  notifyStorageChanged();
}

export function trashExpiresAt(deletedAt: string): Date {
  return new Date(parseApiDate(deletedAt).getTime() + TRASH_RETENTION_MS);
}

/** Whole days left before permanent purge, at least 0. */
export function trashDaysLeft(deletedAt: string): number {
  const ms = trashExpiresAt(deletedAt).getTime() - Date.now();
  return Math.max(0, Math.ceil(ms / (24 * 60 * 60 * 1000)));
}

/** One document's outcome in a bulk request. ``ok`` is false with a reason when it could not be acted on. */
export type BulkResult = {
  document_id: string;
  ok: boolean;
  message: string | null;
};

/**
 * Reprocess several documents at once. The server acts on each id it can and
 * reports the rest (e.g. a document whose job is still running), so the caller
 * can drop the ones it started and keep the ones it did not.
 */
export async function bulkReprocess(
  documentIds: string[]
): Promise<BulkResult[]> {
  const res = await api.post<BulkResult[]>('/api/v1/documents/bulk-reprocess', {
    document_ids: documentIds,
  });
  return res.data;
}

/**
 * Move several documents to trash at once. The server acts on each id it
 * can and reports the rest (e.g. a document a worker is saving results for),
 * so the caller can drop the ones it deleted and keep the ones that failed.
 */
export async function bulkDelete(documentIds: string[]): Promise<BulkResult[]> {
  const res = await api.post<BulkResult[]>('/api/v1/documents/bulk-delete', {
    document_ids: documentIds,
  });
  notifyStorageChanged();
  return res.data;
}

/**
 * A server-side rejection. When the server marks it retryable (a problem on
 * its side, like storage being briefly down), it is reported as a failure so
 * the page offers Retry; otherwise the file itself is the problem. A
 * rejection carrying ``duplicate_of`` is a duplicate warning (US-79).
 */
function fromRejection(rejected: UploadRejection | undefined): UploadOutcome {
  const reason = rejected?.reason ?? 'The file was not accepted.';
  const duplicate = rejected?.duplicate_of;
  if (duplicate) {
    return {
      kind: 'duplicate',
      reason,
      duplicateOf: {
        documentId: duplicate.document_id,
        filename: duplicate.filename,
        status: duplicate.status ?? undefined,
      },
    };
  }
  return rejected?.retryable
    ? { kind: 'failed', message: reason }
    : { kind: 'rejected', reason };
}
