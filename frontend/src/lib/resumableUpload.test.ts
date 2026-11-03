/** US-80: chunked uploads that resume from the server's confirmed chunks. */
import { createHash, randomBytes } from 'node:crypto';
import { AxiosError, AxiosHeaders, CanceledError } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import api from './api';
import {
  CHUNK_ATTEMPTS,
  RESUMABLE_THRESHOLD_BYTES,
  SESSIONS_STORAGE_KEY,
  isResumable,
  rememberSession,
  storedSessionFor,
  uploadResumable,
  type UploadSession,
} from './resumableUpload';

vi.mock('./api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

// Small chunks keep the real bytes (and hashing them) cheap in tests.
const CHUNK = 1024;
const SIZE = CHUNK * 2 + 100; // three chunks, a short last one
const CONTENT = new Uint8Array(randomBytes(SIZE));

const sha = (data: Uint8Array) =>
  createHash('sha256').update(data).digest('hex');
const chunkOf = (content: Uint8Array, index: number) =>
  content.subarray(index * CHUNK, (index + 1) * CHUNK);

/** The file the server's session was started with. */
function bigFile(content: Uint8Array = CONTENT): File {
  return new File([content as Uint8Array<ArrayBuffer>], 'scan.pdf', {
    type: 'application/pdf',
    lastModified: 1,
  });
}

/** A different file that shares name, size and timestamp with bigFile(). */
function lookalikeFile(): File {
  const other = new Uint8Array(CONTENT);
  other[CHUNK + 5] ^= 0xff; // only chunk 1 differs
  return bigFile(other);
}

/** The server's view; checksums of confirmed chunks come from CONTENT. */
function session(overrides: Partial<UploadSession> = {}): UploadSession {
  const received = overrides.received_chunks ?? [];
  return {
    id: 'session-1',
    filename: 'scan.pdf',
    total_size_bytes: SIZE,
    chunk_size_bytes: CHUNK,
    chunk_count: 3,
    received_chunks: received,
    received_checksums: received.map((index) => sha(chunkOf(CONTENT, index))),
    received_bytes: 0,
    status: 'processing',
    error_message: null,
    expires_at: '2026-11-04T12:00:00',
    batch_id: null,
    document_id: null,
    file_sha256: null,
    ...overrides,
  };
}

function finalizeBodies() {
  return vi
    .mocked(api.post)
    .mock.calls.filter(([url]) => String(url).endsWith('/finalize'))
    .map(([, body]) => body);
}

function httpError(status: number, detail: unknown): AxiosError {
  const config = { headers: new AxiosHeaders() };
  return new AxiosError('Request failed', 'ERR', config, null, {
    status,
    statusText: '',
    headers: {},
    config,
    data: { detail },
  });
}

/** No reply at all, as when the connection drops or times out. */
function networkError(): AxiosError {
  return new AxiosError('timeout of 0ms exceeded', 'ECONNABORTED', {
    headers: new AxiosHeaders(),
  });
}

const finalized = {
  data: { document_id: 'doc-1', filename: 'scan.pdf', status: 'queued' },
};

function sentIndexes(): number[] {
  return vi
    .mocked(api.put)
    .mock.calls.map(([url]) => Number(String(url).split('/').pop()));
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.clearAllMocks();
});

it('sends only files above the threshold in chunks', () => {
  const sized = (size: number) => {
    const file = new File(['x'], 'a.pdf');
    Object.defineProperty(file, 'size', { value: size });
    return file;
  };
  expect(isResumable(sized(RESUMABLE_THRESHOLD_BYTES))).toBe(false);
  expect(isResumable(sized(RESUMABLE_THRESHOLD_BYTES + 1))).toBe(true);
});

describe('uploadResumable', () => {
  it('uploads every chunk, finalizes once and forgets the session', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: session() }) // create
      .mockResolvedValueOnce(finalized); // finalize
    vi.mocked(api.put).mockResolvedValue({ data: {} });
    const file = bigFile();
    const progress: number[] = [];

    const outcome = await uploadResumable(file, {
      batchId: 'batch-1',
      onProgress: (n) => progress.push(n),
      chunkSizeBytes: CHUNK,
    });

    expect(outcome).toEqual({
      kind: 'accepted',
      documentId: 'doc-1',
      processingStarted: true,
      message: undefined,
    });
    expect(api.post).toHaveBeenNthCalledWith(1, '/api/v1/uploads', {
      filename: 'scan.pdf',
      total_size_bytes: SIZE,
      chunk_size_bytes: CHUNK,
      batch_id: 'batch-1',
    });
    expect(sentIndexes()).toEqual([0, 1, 2]);
    // Each chunk carries its checksum, and finalize the whole file's.
    const firstPut = vi.mocked(api.put).mock.calls[0][2] as {
      headers: Record<string, string>;
    };
    expect(firstPut.headers['X-Chunk-SHA256']).toBe(sha(chunkOf(CONTENT, 0)));
    expect(api.post).toHaveBeenLastCalledWith(
      '/api/v1/uploads/session-1/finalize',
      { sha256: sha(CONTENT), batch_id: 'batch-1' }
    );
    expect(progress.at(-1)).toBe(100);
    expect(storedSessionFor(file)).toBeNull();
  });

  it('resumes from the chunks the server already confirmed', async () => {
    const file = bigFile();
    // An earlier page load got two chunks through before the connection
    // dropped.
    rememberSession(file, 'session-1');
    vi.mocked(api.get).mockResolvedValueOnce({
      data: session({
        received_chunks: [0, 1],
        received_bytes: CHUNK * 2,
      }),
    });
    vi.mocked(api.put).mockResolvedValue({ data: {} });
    vi.mocked(api.post).mockResolvedValueOnce(finalized);
    const progress: number[] = [];

    const outcome = await uploadResumable(file, {
      onProgress: (n) => progress.push(n),
    });

    expect(outcome.kind).toBe('accepted');
    expect(api.get).toHaveBeenCalledWith('/api/v1/uploads/session-1');
    // No new session, and only the missing chunk is sent.
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(sentIndexes()).toEqual([2]);
    // Progress starts from what was already on the server.
    expect(progress[0]).toBe(Math.floor(((CHUNK * 2) / SIZE) * 100));
    // The whole-file hash covers the confirmed chunks it did not resend.
    expect(finalizeBodies()).toEqual([{ sha256: sha(CONTENT) }]);
  });

  it('does not resume a different file with the same name, size and date', async () => {
    const file = lookalikeFile();
    rememberSession(file, 'session-1');
    // The server holds chunks 0 and 1 of the original file.
    vi.mocked(api.get).mockResolvedValueOnce({
      data: session({ received_chunks: [0, 1], received_bytes: CHUNK * 2 }),
    });
    vi.mocked(api.delete).mockResolvedValueOnce({});
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: session({ id: 'fresh-session' }) })
      .mockResolvedValueOnce(finalized);
    vi.mocked(api.put).mockResolvedValue({ data: {} });

    const outcome = await uploadResumable(file, { chunkSizeBytes: CHUNK });

    expect(outcome.kind).toBe('accepted');
    // The old session is thrown away and nothing more is sent to it...
    expect(api.delete).toHaveBeenCalledWith('/api/v1/uploads/session-1');
    // ...and this file goes up in full, to a new session, from chunk 0.
    expect(vi.mocked(api.put).mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/uploads/fresh-session/chunks/0',
      '/api/v1/uploads/fresh-session/chunks/1',
      '/api/v1/uploads/fresh-session/chunks/2',
    ]);
    const local = new Uint8Array(await file.arrayBuffer());
    expect(finalizeBodies()).toEqual([{ sha256: sha(local) }]);
    expect(storedSessionFor(file)).toBeNull();
  });

  it('checks every confirmed chunk, not only the first', async () => {
    // Chunks 0 and 2 confirmed; the lookalike differs only in chunk 1, so
    // it still matches both and may resume, sending its own chunk 1.
    const file = lookalikeFile();
    rememberSession(file, 'session-1');
    vi.mocked(api.get).mockResolvedValueOnce({
      data: session({ received_chunks: [0, 2], received_bytes: CHUNK + 100 }),
    });
    vi.mocked(api.put).mockResolvedValue({ data: {} });
    vi.mocked(api.post).mockResolvedValueOnce(finalized);

    expect((await uploadResumable(file)).kind).toBe('accepted');
    expect(sentIndexes()).toEqual([1]);
    expect(api.delete).not.toHaveBeenCalled();
    // The server re-checks the assembled file against this file's hash.
    const local = new Uint8Array(await file.arrayBuffer());
    expect(finalizeBodies()).toEqual([{ sha256: sha(local) }]);
  });

  it('finishes a resumed upload in the run on screen (US-31)', async () => {
    const file = bigFile();
    // Started in an earlier run, before the page was reloaded.
    rememberSession(file, 'session-1');
    vi.mocked(api.get).mockResolvedValueOnce({
      data: session({
        received_chunks: [0],
        received_bytes: CHUNK,
        batch_id: 'old-run',
      }),
    });
    vi.mocked(api.put).mockResolvedValue({ data: {} });
    vi.mocked(api.post).mockResolvedValueOnce(finalized);

    await uploadResumable(file, { batchId: 'new-run' });

    expect(finalizeBodies()).toEqual([
      { sha256: sha(CONTENT), batch_id: 'new-run' },
    ]);
  });

  it('a finished session is not reported for a different file', async () => {
    const file = lookalikeFile();
    rememberSession(file, 'session-1');
    // The finalize reply was lost; the server has the original's document.
    vi.mocked(api.get).mockResolvedValueOnce({
      data: session({
        status: 'completed',
        received_chunks: [0, 1, 2],
        received_checksums: [],
        received_bytes: SIZE,
        file_sha256: sha(CONTENT),
      }),
    });
    vi.mocked(api.post)
      .mockRejectedValueOnce(
        httpError(409, {
          detail: 'This upload was finished with a different file.',
          code: 'UPLOAD_FILE_MISMATCH',
        })
      )
      .mockResolvedValueOnce({ data: session({ id: 'fresh-session' }) })
      .mockResolvedValueOnce({
        data: { document_id: 'doc-2', filename: 'scan.pdf', status: 'queued' },
      });
    vi.mocked(api.put).mockResolvedValue({ data: {} });

    const outcome = await uploadResumable(file, { chunkSizeBytes: CHUNK });

    expect(outcome).toMatchObject({ kind: 'accepted', documentId: 'doc-2' });
    expect(sentIndexes()).toEqual([0, 1, 2]);
  });

  it('starts over when the remembered session has expired', async () => {
    const file = bigFile();
    rememberSession(file, 'old-session');
    vi.mocked(api.get).mockRejectedValueOnce(httpError(410, 'expired'));
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: session({ id: 'new-session' }) })
      .mockResolvedValueOnce(finalized);
    vi.mocked(api.put).mockResolvedValue({ data: {} });

    const outcome = await uploadResumable(file);

    expect(outcome.kind).toBe('accepted');
    expect(sentIndexes()).toEqual([0, 1, 2]);
    expect(vi.mocked(api.put).mock.calls[0][0]).toBe(
      '/api/v1/uploads/new-session/chunks/0'
    );
  });

  it('retries a chunk after a network timeout without resending the others', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: session() })
      .mockResolvedValueOnce(finalized);
    vi.mocked(api.put)
      .mockResolvedValueOnce({ data: {} }) // chunk 0
      .mockRejectedValueOnce(networkError()) // chunk 1 times out...
      .mockResolvedValue({ data: {} }); // ...and goes through on retry

    const outcome = await uploadResumable(bigFile(), { retryDelayMs: 0 });

    expect(outcome.kind).toBe('accepted');
    expect(sentIndexes()).toEqual([0, 1, 1, 2]);
  });

  it('keeps the session when a chunk keeps failing, so Retry resumes', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: session() });
    vi.mocked(api.put)
      .mockResolvedValueOnce({ data: {} })
      .mockRejectedValue(networkError());
    const file = bigFile();

    const outcome = await uploadResumable(file, { retryDelayMs: 0 });

    expect(outcome).toEqual({
      kind: 'failed',
      message:
        'Could not reach the server. Check your connection and try again.',
    });
    expect(sentIndexes()).toEqual([0, ...Array(CHUNK_ATTEMPTS).fill(1)]);
    expect(storedSessionFor(file)?.sessionId).toBe('session-1');

    // Retry: the server confirms chunk 0, so the upload continues at 1.
    vi.mocked(api.put).mockReset();
    vi.mocked(api.put).mockResolvedValue({ data: {} });
    vi.mocked(api.get).mockResolvedValueOnce({
      data: session({ received_chunks: [0], received_bytes: CHUNK }),
    });
    vi.mocked(api.post).mockResolvedValueOnce(finalized);
    expect((await uploadResumable(file)).kind).toBe('accepted');
    expect(sentIndexes()).toEqual([1, 2]);
  });

  it('pauses before the next chunk and resumes where it stopped', async () => {
    const controller = new AbortController();
    vi.mocked(api.post).mockResolvedValueOnce({ data: session() });
    vi.mocked(api.put).mockImplementationOnce(async () => {
      // The user presses Pause while chunk 0 is on its way.
      controller.abort();
      return { data: {} };
    });
    const file = bigFile();

    const outcome = await uploadResumable(file, { signal: controller.signal });

    expect(outcome).toEqual({ kind: 'paused' });
    expect(sentIndexes()).toEqual([0]);
    expect(api.post).toHaveBeenCalledTimes(1); // never finalized
    expect(storedSessionFor(file)?.sessionId).toBe('session-1');

    vi.mocked(api.get).mockResolvedValueOnce({
      data: session({ received_chunks: [0], received_bytes: CHUNK }),
    });
    vi.mocked(api.put).mockResolvedValue({ data: {} });
    vi.mocked(api.post).mockResolvedValueOnce(finalized);
    expect((await uploadResumable(file)).kind).toBe('accepted');
    expect(sentIndexes()).toEqual([0, 1, 2]);
  });

  it('pausing mid-chunk cancels the request and reports paused', async () => {
    const controller = new AbortController();
    vi.mocked(api.post).mockResolvedValueOnce({ data: session() });
    vi.mocked(api.put).mockImplementationOnce(async () => {
      controller.abort();
      throw new CanceledError();
    });

    const outcome = await uploadResumable(bigFile(), {
      signal: controller.signal,
    });

    expect(outcome).toEqual({ kind: 'paused' });
    expect(api.put).toHaveBeenCalledTimes(1);
  });

  it('reports a quota rejection with the server message', async () => {
    vi.mocked(api.post).mockRejectedValueOnce(
      httpError(413, 'Not enough storage left in your account for this file.')
    );
    const file = bigFile();

    const outcome = await uploadResumable(file);

    expect(outcome).toEqual({
      kind: 'rejected',
      reason: 'Not enough storage left in your account for this file.',
    });
    expect(api.put).not.toHaveBeenCalled();
    expect(storedSessionFor(file)).toBeNull();
  });

  it('rejects a file the server refused after its first chunk', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: session() });
    vi.mocked(api.put).mockRejectedValueOnce(
      httpError(400, 'Unsupported file type.')
    );
    vi.mocked(api.get).mockResolvedValueOnce({
      data: session({ status: 'failed' }),
    });
    const file = bigFile();

    const outcome = await uploadResumable(file);

    expect(outcome).toEqual({
      kind: 'rejected',
      reason: 'Unsupported file type.',
    });
    expect(storedSessionFor(file)).toBeNull();
  });

  it('survives storage being unavailable', async () => {
    const getItem = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => {
        throw new Error('blocked');
      });
    const setItem = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new Error('blocked');
      });
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: session() })
      .mockResolvedValueOnce(finalized);
    vi.mocked(api.put).mockResolvedValue({ data: {} });

    expect((await uploadResumable(bigFile())).kind).toBe('accepted');
    getItem.mockRestore();
    setItem.mockRestore();
    expect(localStorage.getItem(SESSIONS_STORAGE_KEY)).toBeNull();
  });
});

describe('uploadResumable duplicates (US-79)', () => {
  const duplicateDetail = {
    detail: 'You already have an identical document in your library: a.pdf.',
    code: 'UPLOAD_DUPLICATE',
    filename: 'scan.pdf',
    reason: 'You already have an identical document in your library: a.pdf.',
    retryable: false,
    duplicate_of: { document_id: 'doc-0', filename: 'a.pdf', status: 'queued' },
  };

  it('reports a duplicate refused at finalize and keeps the session', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: session() }) // create
      .mockRejectedValueOnce(httpError(409, duplicateDetail)); // finalize
    vi.mocked(api.put).mockResolvedValue({ data: {} });
    const file = bigFile();

    const outcome = await uploadResumable(file, { chunkSizeBytes: CHUNK });

    expect(outcome).toEqual({
      kind: 'duplicate',
      reason: duplicateDetail.reason,
      duplicateOf: { documentId: 'doc-0', filename: 'a.pdf', status: 'queued' },
    });
    // Kept for Upload anyway; nothing was aborted.
    expect(storedSessionFor(file)?.sessionId).toBe('session-1');
    expect(api.delete).not.toHaveBeenCalled();
    expect(finalizeBodies()).toEqual([{ sha256: sha(CONTENT) }]);
  });

  it('Upload anyway finalizes the same chunks with the flag, sending none', async () => {
    const file = bigFile();
    rememberSession(file, 'session-1');
    // Every chunk is on the server already; only finalize was refused.
    vi.mocked(api.get).mockResolvedValueOnce({
      data: session({ received_chunks: [0, 1, 2], received_bytes: SIZE }),
    });
    vi.mocked(api.post).mockResolvedValueOnce(finalized);

    const outcome = await uploadResumable(file, {
      batchId: 'batch-1',
      allowDuplicate: true,
    });

    expect(outcome.kind).toBe('accepted');
    expect(api.put).not.toHaveBeenCalled();
    expect(api.post).toHaveBeenCalledTimes(1); // no new session
    expect(finalizeBodies()).toEqual([
      { sha256: sha(CONTENT), batch_id: 'batch-1', allow_duplicate: true },
    ]);
    expect(storedSessionFor(file)).toBeNull();
  });

  it('other 409s at finalize are still failures to retry', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: session() })
      .mockRejectedValueOnce(
        httpError(409, '1 of 3 parts have not been uploaded yet.')
      );
    vi.mocked(api.put).mockResolvedValue({ data: {} });

    const outcome = await uploadResumable(bigFile(), { chunkSizeBytes: CHUNK });

    expect(outcome).toEqual({
      kind: 'failed',
      message: '1 of 3 parts have not been uploaded yet.',
    });
  });
});
