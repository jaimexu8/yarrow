import { isAxiosError, isCancel } from 'axios';
import api from './api';
import type { UploadOutcome } from './documents';
import { toApiError } from './errors';
import { Sha256, sha256Hex } from './sha256';
import { notifyStorageChanged } from './storage';

/**
 * Resumable uploads of large files (US-80).
 *
 * Files above RESUMABLE_THRESHOLD_BYTES are sent in chunks: the server keeps
 * every confirmed chunk, so after a dropped connection, a pause or a page
 * reload the upload continues from where it stopped instead of starting
 * over. Smaller files keep the single-request upload in documents.ts.
 *
 * Keep the chunk size within MIN_CHUNK_BYTES..MAX_CHUNK_BYTES in
 * backend/app/api/v1/endpoints/uploads.py.
 */
export const RESUMABLE_THRESHOLD_BYTES = 8 * 1024 * 1024;
export const CHUNK_SIZE_BYTES = 4 * 1024 * 1024;
/** Attempts per chunk before the file is reported as failed (Retry resumes). */
export const CHUNK_ATTEMPTS = 3;
const RETRY_DELAY_MS = 1000;
/** When the file changed while it was being sent from scratch. */
const CHANGED_MESSAGE =
  'The file changed while it was being uploaded. Choose it again and retry.';

/** Where unfinished sessions are remembered, so a reload can resume them. */
export const SESSIONS_STORAGE_KEY = 'yarrow.upload.sessions';

export function isResumable(file: File): boolean {
  return file.size > RESUMABLE_THRESHOLD_BYTES;
}

/** Matches UploadSessionOut on the server. */
export type UploadSession = {
  id: string;
  filename: string;
  total_size_bytes: number;
  chunk_size_bytes: number;
  chunk_count: number;
  received_chunks: number[];
  /** sha256 of each confirmed chunk, in the order of received_chunks. */
  received_checksums: string[];
  received_bytes: number;
  status: 'processing' | 'completed' | 'failed' | 'canceled';
  error_message: string | null;
  expires_at: string;
  batch_id: string | null;
  document_id: string | null;
  file_sha256: string | null;
};

type FinalizeResponse = {
  document_id: string;
  filename: string;
  status?: string;
  message?: string | null;
};

/** An unfinished upload remembered in this browser. */
export type StoredSession = {
  sessionId: string;
  filename: string;
  size: number;
};

/** Identifies "the same file" across reloads, without reading its bytes. */
export function fingerprint(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function readSessions(): Record<string, StoredSession> {
  try {
    const raw = storedSessionsSnapshot();
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    // Storage blocked or unreadable: uploads still work, just not across
    // reloads.
    return {};
  }
}

const listeners = new Set<() => void>();
let inMemorySessions: string | null = null;

/** For useSyncExternalStore: notified whenever the remembered set changes. */
export function subscribeStoredSessions(notify: () => void): () => void {
  listeners.add(notify);
  return () => {
    listeners.delete(notify);
  };
}

/** A stable snapshot (the raw stored string) for useSyncExternalStore. */
export function storedSessionsSnapshot(): string | null {
  try {
    return localStorage.getItem(SESSIONS_STORAGE_KEY);
  } catch {
    return inMemorySessions;
  }
}

/** Every unfinished upload in a snapshot, for a "pick up" hint. */
export function parseStoredSessions(snapshot: string | null): StoredSession[] {
  try {
    const parsed = snapshot ? JSON.parse(snapshot) : {};
    return parsed && typeof parsed === 'object' ? Object.values(parsed) : [];
  } catch {
    return [];
  }
}

function writeSessions(sessions: Record<string, StoredSession>): void {
  inMemorySessions = Object.keys(sessions).length
    ? JSON.stringify(sessions)
    : null;
  try {
    if (inMemorySessions === null) {
      localStorage.removeItem(SESSIONS_STORAGE_KEY);
    } else {
      localStorage.setItem(SESSIONS_STORAGE_KEY, inMemorySessions);
    }
  } catch {
    // Kept for this page only.
  }
  listeners.forEach((notify) => notify());
}

export function rememberSession(file: File, sessionId: string): void {
  writeSessions({
    ...readSessions(),
    [fingerprint(file)]: { sessionId, filename: file.name, size: file.size },
  });
}

export function forgetSession(file: File): void {
  const sessions = readSessions();
  delete sessions[fingerprint(file)];
  writeSessions(sessions);
}

export function storedSessionFor(file: File): StoredSession | null {
  return readSessions()[fingerprint(file)] ?? null;
}

export async function createUploadSession(
  file: File,
  batchId?: string,
  chunkSizeBytes: number = CHUNK_SIZE_BYTES
): Promise<UploadSession> {
  const res = await api.post<UploadSession>('/api/v1/uploads', {
    filename: file.name,
    total_size_bytes: file.size,
    chunk_size_bytes: chunkSizeBytes,
    ...(batchId && { batch_id: batchId }),
  });
  notifyStorageChanged();
  return res.data;
}

export async function getUploadSession(id: string): Promise<UploadSession> {
  const res = await api.get<UploadSession>(`/api/v1/uploads/${id}`);
  return res.data;
}

export async function abortUploadSession(id: string): Promise<void> {
  await api.delete(`/api/v1/uploads/${id}`);
  notifyStorageChanged();
}

/** A chunk's bytes. One chunk at a time, so memory stays at one chunk. */
async function readChunk(
  file: File,
  index: number,
  chunkSize: number
): Promise<Uint8Array> {
  const blob = file.slice(index * chunkSize, (index + 1) * chunkSize);
  if (typeof blob.arrayBuffer === 'function') {
    return new Uint8Array(await blob.arrayBuffer());
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

function status(err: unknown): number | null {
  return isAxiosError(err) ? (err.response?.status ?? null) : null;
}

function errorCode(err: unknown): string | undefined {
  return toApiError(err).code;
}

/** Worth sending again: no reply at all, or a problem on the server's side. */
function isTransient(err: unknown): boolean {
  const code = status(err);
  return code === null || code >= 500;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

class Paused extends Error {}

/** The local file is not the one the remembered session was uploading. */
class DifferentFile extends Error {}

export type ResumableOutcome = UploadOutcome | { kind: 'paused' };

type Options = {
  batchId?: string;
  /** Percent of the whole file the server has confirmed or is receiving. */
  onProgress?: (percent: number) => void;
  /** Aborting pauses: the confirmed chunks stay on the server. */
  signal?: AbortSignal;
  /** Wait between attempts at a chunk; tests pass 0. */
  retryDelayMs?: number;
  /** Chunk size for new sessions; tests use the server's minimum. */
  chunkSizeBytes?: number;
  /**
   * "Upload anyway" (US-79): finalize even though the user already has an
   * identical document. Without it, such a file comes back as ``duplicate``
   * and its session is kept, so asking again finalizes the same chunks.
   */
  allowDuplicate?: boolean;
};

/**
 * The session to continue for this file: the remembered one if the server
 * still has it (unless ``fresh``), otherwise a new one. Returns an outcome
 * instead when the server refuses the file outright.
 */
async function openSession(
  file: File,
  options: Options,
  fresh: boolean
): Promise<UploadSession | UploadOutcome> {
  const stored = fresh ? null : storedSessionFor(file);
  if (stored) {
    try {
      const session = await getUploadSession(stored.sessionId);
      if (
        session.total_size_bytes === file.size &&
        (session.status === 'processing' || session.status === 'completed')
      ) {
        return session;
      }
    } catch (err) {
      // Gone (expired, aborted, another account's): start over. Anything
      // else means the server could not be asked; report it so Retry can
      // try the same session again.
      const code = status(err);
      if (code !== 404 && code !== 410) {
        return { kind: 'failed', message: toApiError(err).message };
      }
    }
    forgetSession(file);
  }
  try {
    const session = await createUploadSession(
      file,
      options.batchId,
      options.chunkSizeBytes
    );
    rememberSession(file, session.id);
    return session;
  } catch (err) {
    const code = status(err);
    const message = toApiError(err).message;
    // 400/413: the type, size or quota rules refuse this file.
    if (code === 400 || code === 413)
      return { kind: 'rejected', reason: message };
    return { kind: 'failed', message };
  }
}

async function sendChunk(
  session: UploadSession,
  index: number,
  bytes: Uint8Array,
  options: Options,
  onChunkProgress: (loaded: number) => void
): Promise<void> {
  const headers = {
    'Content-Type': 'application/octet-stream',
    'X-Chunk-SHA256': sha256Hex(bytes),
  };
  const body = new Blob([bytes as Uint8Array<ArrayBuffer>]);

  for (let attempt = 1; ; attempt += 1) {
    if (options.signal?.aborted) throw new Paused();
    try {
      await api.put(`/api/v1/uploads/${session.id}/chunks/${index}`, body, {
        headers,
        signal: options.signal,
        onUploadProgress: (event) => onChunkProgress(event.loaded ?? 0),
      });
      return;
    } catch (err) {
      if (isCancel(err) || options.signal?.aborted) throw new Paused();
      // A timeout or dropped connection: send the same bytes again. If the
      // first attempt did arrive, the server says so and stores nothing new.
      if (!isTransient(err) || attempt >= CHUNK_ATTEMPTS) throw err;
      onChunkProgress(0);
      await wait(
        options.retryDelayMs ?? RETRY_DELAY_MS * attempt,
        options.signal
      );
    }
  }
}

/**
 * Whether the server gave up on the whole upload. A 400 on one chunk can
 * also mean just that chunk arrived damaged, which Retry can fix.
 */
async function sessionFailed(id: string): Promise<boolean> {
  try {
    return (await getUploadSession(id)).status === 'failed';
  } catch {
    return false;
  }
}

/** Matches the finalize 409 for a duplicate: an UploadRejected plus a code. */
const DUPLICATE_CODE = 'UPLOAD_DUPLICATE';

type DuplicateDetail = {
  reason?: string;
  duplicate_of?: { document_id: string; filename: string; status?: string };
};

/**
 * The duplicate warning a finalize was refused with (US-79), if it was one.
 * The session stays open on the server, so Upload anyway can finalize the
 * same chunks again.
 */
function duplicateOutcome(err: unknown): UploadOutcome | null {
  if (!isAxiosError(err) || errorCode(err) !== DUPLICATE_CODE) return null;
  const detail = (err.response?.data as { detail?: DuplicateDetail })?.detail;
  const existing = detail?.duplicate_of;
  if (!existing) return null;
  return {
    kind: 'duplicate',
    reason: detail.reason ?? toApiError(err).message,
    duplicateOf: {
      documentId: existing.document_id,
      filename: existing.filename,
      status: existing.status ?? undefined,
    },
  };
}

function accepted(data: FinalizeResponse): UploadOutcome {
  return {
    kind: 'accepted',
    documentId: data.document_id,
    processingStarted: data.status !== 'failed',
    message: data.message ?? undefined,
  };
}

/**
 * Upload one large file in chunks, continuing a remembered session when the
 * server still has it. Resolves to the same outcomes as uploadDocument,
 * plus ``paused`` when ``signal`` was aborted (call again to resume).
 *
 * A remembered session is only continued with the same file: every chunk
 * the server confirmed is hashed locally and compared with the server's
 * checksum first. Name, size and timestamp alone can match a different
 * file; if any chunk differs, the old session is discarded and the file is
 * uploaded from scratch. The whole file's sha256, built up chunk by chunk,
 * is sent with finalize, so the server refuses a document that is not
 * exactly this file.
 */
export async function uploadResumable(
  file: File,
  options: Options = {},
  fresh = false
): Promise<ResumableOutcome> {
  const opened = await openSession(file, options, fresh);
  if ('kind' in opened) return opened;
  const session = opened;
  const chunkSize = session.chunk_size_bytes;
  const completed = session.status === 'completed';

  const confirmed = new Set(
    completed
      ? Array.from({ length: session.chunk_count }, (_, i) => i)
      : session.received_chunks
  );
  let confirmedBytes = completed
    ? session.total_size_bytes
    : session.received_bytes;
  const report = (inFlight: number) =>
    options.onProgress?.(
      Math.min(
        100,
        Math.floor(
          ((confirmedBytes + inFlight) / session.total_size_bytes) * 100
        )
      )
    );
  report(0);

  // The whole file's hash, fed every chunk in order.
  const whole = new Sha256();
  let hashedUpTo = 0;

  try {
    // Prove the local file is the one whose chunks the server holds before
    // sending anything more.
    const checksums = completed ? [] : session.received_checksums;
    for (const [position, index] of session.received_chunks.entries()) {
      if (completed) break;
      if (options.signal?.aborted) throw new Paused();
      const bytes = await readChunk(file, index, chunkSize);
      if (sha256Hex(bytes) !== checksums[position]) throw new DifferentFile();
      if (index === hashedUpTo) {
        whole.update(bytes);
        hashedUpTo += 1;
      }
    }

    for (let index = hashedUpTo; index < session.chunk_count; index += 1) {
      if (options.signal?.aborted) throw new Paused();
      const bytes = await readChunk(file, index, chunkSize);
      whole.update(bytes);
      if (confirmed.has(index)) continue;
      await sendChunk(session, index, bytes, options, report);
      confirmed.add(index);
      confirmedBytes += bytes.length;
      report(0);
    }
    if (options.signal?.aborted) throw new Paused();
    const res = await api.post<FinalizeResponse>(
      `/api/v1/uploads/${session.id}/finalize`,
      {
        sha256: whole.hex(),
        // After a reload the session may belong to an earlier run; the
        // document joins the run on screen instead (US-31).
        ...(options.batchId && { batch_id: options.batchId }),
        ...(options.allowDuplicate && { allow_duplicate: true }),
      }
    );
    forgetSession(file);
    notifyStorageChanged();
    return accepted(res.data);
  } catch (err) {
    if (err instanceof Paused) return { kind: 'paused' };
    // Every chunk is on the server, but the user already has this file.
    // The session (and the remembered id) is kept for Upload anyway; Skip
    // discards it with discardResumable.
    const duplicate = duplicateOutcome(err);
    if (duplicate) return duplicate;
    if (
      err instanceof DifferentFile ||
      errorCode(err) === 'UPLOAD_FILE_MISMATCH'
    ) {
      // Same name, size and timestamp, different contents. Drop what the
      // old session holds and send this file in full.
      forgetSession(file);
      if (!completed) {
        await abortUploadSession(session.id).catch(() => undefined);
      }
      if (fresh) return { kind: 'failed', message: CHANGED_MESSAGE };
      return uploadResumable(file, options, true);
    }
    const code = status(err);
    const message = toApiError(err).message;
    if (code === 410 || code === 404) {
      // Expired or aborted elsewhere: Retry starts a fresh upload.
      forgetSession(file);
      return { kind: 'failed', message };
    }
    if (code === 400 && (await sessionFailed(session.id))) {
      // The file itself was refused (wrong type, damaged, checksum). The
      // server has dropped it, and resuming cannot help.
      forgetSession(file);
      return { kind: 'rejected', reason: message };
    }
    // Network, server, quota (413) or a conflict: the session is kept, so
    // Retry continues from the confirmed chunks.
    return { kind: 'failed', message };
  }
}

/** Remove what the server holds of an unfinished upload, best effort. */
export async function discardResumable(file: File): Promise<void> {
  const stored = storedSessionFor(file);
  if (!stored) return;
  forgetSession(file);
  try {
    await abortUploadSession(stored.sessionId);
  } catch {
    // The server sweeps abandoned uploads after the retention window.
  }
}
