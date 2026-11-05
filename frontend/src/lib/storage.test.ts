import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import api from './api';
import {
  bulkDelete,
  deleteDocument,
  emptyTrash,
  permanentlyDeleteDocument,
  restoreDocument,
  uploadDocument,
} from './documents';
import {
  abortUploadSession,
  createUploadSession,
  uploadResumable,
} from './resumableUpload';
import {
  formatBytes,
  getStorageUsage,
  notifyStorageChanged,
  STORAGE_CHANGED_EVENT,
} from './storage';

vi.mock('./api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const KB = 1024;
const MB = 1024 * KB;
const GB = 1024 * MB;
const TB = 1024 * GB;

describe('formatBytes', () => {
  it.each([
    [0, '0 B'],
    [1, '1 B'],
    [1023, '1023 B'],
    [1024, '1 KB'],
    [1536, '1.5 KB'],
    [9.95 * KB, '9.9 KB'],
    [9.96 * KB, '10 KB'],
    [10 * KB, '10 KB'],
    [10.6 * KB, '11 KB'],
    [1023.6 * KB, '1 MB'],
    [4194304, '4 MB'],
    [125829120, '120 MB'],
    [256 * MB, '256 MB'],
    [1073741824, '1 GB'],
    [1610612736, '1.5 GB'],
    [TB, '1 TB'],
    [1.5 * TB, '1.5 TB'],
    [2048 * TB, '2048 TB'],
  ])('formats %d as %s', (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});

describe('getStorageUsage', () => {
  it('reads the current user storage endpoint', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: { used_bytes: 1 } });
    await expect(getStorageUsage()).resolves.toEqual({ used_bytes: 1 });
    expect(api.get).toHaveBeenCalledWith('/api/v1/auth/me/storage');
  });
});

describe('storage change notifications', () => {
  const listener = vi.fn();

  beforeEach(() => {
    window.addEventListener(STORAGE_CHANGED_EVENT, listener);
  });

  afterEach(() => {
    window.removeEventListener(STORAGE_CHANGED_EVENT, listener);
    vi.clearAllMocks();
  });

  it('dispatches a window CustomEvent', () => {
    notifyStorageChanged();
    expect(STORAGE_CHANGED_EVENT).toBe('yarrow:storage-changed');
    expect(listener).toHaveBeenCalledOnce();
    expect(listener.mock.calls[0][0]).toBeInstanceOf(CustomEvent);
  });

  it.each([
    ['deleteDocument', () => deleteDocument('doc-1')],
    ['permanentlyDeleteDocument', () => permanentlyDeleteDocument('doc-1')],
    ['emptyTrash', () => emptyTrash()],
    ['abortUploadSession', () => abortUploadSession('session-1')],
  ])('fires after %s', async (_name, call) => {
    vi.mocked(api.delete).mockResolvedValueOnce({});
    await call();
    expect(listener).toHaveBeenCalledOnce();
  });

  it.each([
    ['restoreDocument', () => restoreDocument('doc-1')],
    ['bulkDelete', () => bulkDelete(['doc-1'])],
    [
      'createUploadSession',
      () => createUploadSession(new File(['x'], 'big.pdf')),
    ],
  ])('fires after %s', async (_name, call) => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: [] });
    await call();
    expect(listener).toHaveBeenCalledOnce();
  });

  it('does not fire when a delete fails', async () => {
    vi.mocked(api.delete).mockRejectedValueOnce(new Error('offline'));
    await expect(deleteDocument('doc-1')).rejects.toThrow('offline');
    expect(listener).not.toHaveBeenCalled();
  });

  it('fires after an accepted upload', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({
      data: {
        accepted: [
          { document_id: 'doc-1', filename: 'a.pdf', status: 'queued' },
        ],
        rejected: [],
      },
    });
    const outcome = await uploadDocument(
      new File(['x'], 'a.pdf'),
      'upload-1',
      () => undefined
    );
    expect(outcome.kind).toBe('accepted');
    expect(listener).toHaveBeenCalledOnce();
  });

  it('does not fire when an upload is rejected', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({
      data: {
        accepted: [],
        rejected: [{ filename: 'a.pdf', reason: 'Storage quota exceeded' }],
      },
    });
    const outcome = await uploadDocument(
      new File(['x'], 'a.pdf'),
      'upload-1',
      () => undefined
    );
    expect(outcome.kind).toBe('rejected');
    expect(listener).not.toHaveBeenCalled();
  });

  it('fires after a resumable upload is finalized', async () => {
    const file = new File([new Uint8Array(100)], 'large.pdf');
    vi.mocked(api.post)
      .mockResolvedValueOnce({
        data: {
          id: 'session-1',
          filename: 'large.pdf',
          total_size_bytes: 100,
          chunk_size_bytes: 100,
          chunk_count: 1,
          received_chunks: [],
          received_checksums: [],
          received_bytes: 0,
          status: 'processing',
          expires_at: '2026-11-05T12:00:00',
        },
      })
      .mockResolvedValueOnce({
        data: { document_id: 'doc-1', filename: 'large.pdf', status: 'queued' },
      });
    vi.mocked(api.put).mockResolvedValue({ data: {} });

    const outcome = await uploadResumable(file, { chunkSizeBytes: 100 });

    expect(outcome.kind).toBe('accepted');
    expect(listener).toHaveBeenCalledTimes(2);
  });
});
