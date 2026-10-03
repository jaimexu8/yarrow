import { isAxiosError } from 'axios';
import api from './api';
import { toApiError } from './errors';

/** Matches Document.status on the backend. */
export type DocumentStatus =
  'queued' | 'processing' | 'completed' | 'failed' | 'canceled';

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

export type DocumentSummary = {
  id: string;
  filename: string;
  file_size_bytes: number;
  file_type: string;
  page_count: number | null;
  status: DocumentStatus | null;
  error_message: string | null;
  created_at: string | null;
  reprocess?: ReprocessInfo | null;
};

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

export async function listDocuments(): Promise<DocumentSummary[]> {
  const res = await api.get<DocumentSummary[]>('/api/v1/documents/');
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
 * Cancel a queued document's processing (US-42). The server refuses (409)
 * once a worker has started it, so this can fail even if the list still
 * showed "Queued" a moment ago.
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

type UploadResponse = {
  accepted: {
    document_id: string;
    filename: string;
    status?: string;
    message?: string | null;
  }[];
  rejected: { filename: string; reason: string; retryable?: boolean }[];
};

/**
 * What happened to one file:
 * - accepted: stored. ``processingStarted`` is false when the file was kept
 *   but processing could not be started (``message`` says why).
 * - rejected: the server refused this file, with a reason (type, size, quota)
 * - failed: the upload itself went wrong (network, server error). Retrying
 *   with the same upload id is always safe.
 */
export type UploadOutcome =
  | {
      kind: 'accepted';
      documentId: string;
      processingStarted: boolean;
      message?: string;
    }
  | { kind: 'rejected'; reason: string }
  | { kind: 'failed'; message: string };

/**
 * Upload a single file (US-7). The page sends files one at a time so each has
 * its own progress and result, and one failure can't affect the rest (US-8).
 */
export async function uploadDocument(
  file: File,
  uploadId: string,
  onProgress: (percent: number) => void
): Promise<UploadOutcome> {
  const form = new FormData();
  form.append('files', file);
  form.append('client_upload_ids', uploadId);
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
 * A server-side rejection. When the server marks it retryable (a problem on
 * its side, like storage being briefly down), it is reported as a failure so
 * the page offers Retry; otherwise the file itself is the problem.
 */
function fromRejection(
  rejected: { reason: string; retryable?: boolean } | undefined
): UploadOutcome {
  const reason = rejected?.reason ?? 'The file was not accepted.';
  return rejected?.retryable
    ? { kind: 'failed', message: reason }
    : { kind: 'rejected', reason };
}
