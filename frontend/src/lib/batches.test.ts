/** US-31: the batch id travels with every upload, and progress is read back. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import api from './api';
import {
  getBatchProgress,
  isBatchWorking,
  newBatchId,
  uploadDocument,
  type BatchProgress,
} from './documents';
import {
  BATCH_STORAGE_KEY,
  readStoredBatchId,
  storeBatchId,
} from './useBatchProgress';

vi.mock('./api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

function progress(overrides: Partial<BatchProgress> = {}): BatchProgress {
  return {
    batch_id: 'batch-1',
    total: 0,
    queued: 0,
    processing: 0,
    completed: 0,
    failed: 0,
    canceled: 0,
    created_at: null,
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});

describe('batch ids', () => {
  it('are unique per run', () => {
    expect(newBatchId()).not.toEqual(newBatchId());
  });

  it('uploadDocument sends the batch id with the file', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({
      data: { accepted: [{ document_id: 'doc-1', filename: 'a.pdf' }] },
    });
    const file = new File(['%PDF-1.4'], 'a.pdf', { type: 'application/pdf' });

    const outcome = await uploadDocument(file, 'upload-1', () => {}, 'batch-1');

    expect(outcome).toEqual({
      kind: 'accepted',
      documentId: 'doc-1',
      processingStarted: true,
      message: undefined,
    });
    const form = vi.mocked(api.post).mock.calls[0][1] as FormData;
    expect(form.get('batch_id')).toBe('batch-1');
    expect(form.get('client_upload_ids')).toBe('upload-1');
  });

  it('uploadDocument sends no batch id when there is no run', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({
      data: { accepted: [{ document_id: 'doc-1', filename: 'a.pdf' }] },
    });
    const file = new File(['%PDF-1.4'], 'a.pdf', { type: 'application/pdf' });

    await uploadDocument(file, 'upload-1', () => {});

    const form = vi.mocked(api.post).mock.calls[0][1] as FormData;
    expect(form.has('batch_id')).toBe(false);
  });
});

describe('getBatchProgress', () => {
  it('reads the batch from the API', async () => {
    const counts = progress({ total: 2, queued: 1, completed: 1 });
    vi.mocked(api.get).mockResolvedValueOnce({ data: counts });

    expect(await getBatchProgress('batch-1')).toEqual(counts);
    expect(api.get).toHaveBeenCalledWith('/api/v1/documents/batches/batch-1');
  });
});

describe('isBatchWorking', () => {
  it('is true while anything is queued or processing', () => {
    expect(isBatchWorking(progress({ total: 1, queued: 1 }))).toBe(true);
    expect(isBatchWorking(progress({ total: 1, processing: 1 }))).toBe(true);
  });

  it('is false once every file has finished, failed or been canceled', () => {
    expect(
      isBatchWorking(
        progress({ total: 3, completed: 1, failed: 1, canceled: 1 })
      )
    ).toBe(false);
  });
});

describe('remembered batch id', () => {
  it('survives leaving the page within the tab', () => {
    storeBatchId('batch-1');
    expect(sessionStorage.getItem(BATCH_STORAGE_KEY)).toBe('batch-1');
    expect(readStoredBatchId()).toBe('batch-1');
  });

  it('still shows for this page load when storage is blocked', () => {
    const setItem = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new Error('blocked');
      });
    const getItem = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => {
        throw new Error('blocked');
      });
    try {
      storeBatchId('batch-2');
      expect(readStoredBatchId()).toBe('batch-2');
    } finally {
      setItem.mockRestore();
      getItem.mockRestore();
    }
  });

  it('is forgotten when dismissed', () => {
    storeBatchId('batch-1');
    storeBatchId(null);
    expect(readStoredBatchId()).toBeNull();
  });
});
