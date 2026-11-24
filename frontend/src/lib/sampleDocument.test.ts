import { afterEach, expect, it, vi } from 'vitest';
import { announceBatchActivity, sealBatch } from './batchNotifications';
import { uploadDocument } from './documents';
import {
  SAMPLE_DOCUMENT_FILENAME,
  SAMPLE_DOCUMENT_URL,
  uploadSampleDocument,
} from './sampleDocument';

vi.mock('./documents', () => ({
  newBatchId: () => 'batch-1',
  newUploadId: () => 'upload-1',
  uploadDocument: vi.fn(),
}));

vi.mock('./batchNotifications', () => ({
  sealBatch: vi.fn(async () => {}),
  announceBatchActivity: vi.fn(),
}));

function stubFetch(response: Partial<Response> | Error) {
  const fetchMock = vi.fn(async () => {
    if (response instanceof Error) throw response;
    return response as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

it('sends the bundled PDF through the normal upload as its own batch', async () => {
  const fetchMock = stubFetch({
    ok: true,
    blob: async () => new Blob(['%PDF-sample'], { type: 'application/pdf' }),
  });
  vi.mocked(uploadDocument).mockResolvedValueOnce({
    kind: 'accepted',
    documentId: 'doc-1',
    processingStarted: true,
  });

  const outcome = await uploadSampleDocument();

  expect(fetchMock).toHaveBeenCalledWith(SAMPLE_DOCUMENT_URL);
  const [file, uploadId, , batchId, allowDuplicate] =
    vi.mocked(uploadDocument).mock.calls[0];
  expect(file.name).toBe(SAMPLE_DOCUMENT_FILENAME);
  expect(file.type).toBe('application/pdf');
  expect(file.size).toBeGreaterThan(0);
  expect(uploadId).toBe('upload-1');
  expect(batchId).toBe('batch-1');
  // A second try must not stop on the duplicate warning.
  expect(allowDuplicate).toBe(true);
  expect(sealBatch).toHaveBeenCalledWith('batch-1');
  expect(announceBatchActivity).toHaveBeenCalled();
  expect(outcome).toEqual({
    kind: 'accepted',
    documentId: 'doc-1',
    processingStarted: true,
  });
});

it('still reports success when sealing the batch fails', async () => {
  stubFetch({ ok: true, blob: async () => new Blob(['%PDF']) });
  vi.mocked(uploadDocument).mockResolvedValueOnce({
    kind: 'accepted',
    documentId: 'doc-1',
    processingStarted: true,
  });
  vi.mocked(sealBatch).mockRejectedValueOnce(new Error('offline'));

  const outcome = await uploadSampleDocument();

  expect(outcome.kind).toBe('accepted');
});

it('does not upload anything when the sample file cannot be fetched', async () => {
  stubFetch({ ok: false, status: 404 });

  const outcome = await uploadSampleDocument();

  expect(outcome.kind).toBe('failed');
  expect(uploadDocument).not.toHaveBeenCalled();
  expect(sealBatch).not.toHaveBeenCalled();
});

it('reports a network error while fetching the sample as a failure', async () => {
  stubFetch(new Error('network down'));

  const outcome = await uploadSampleDocument();

  expect(outcome).toEqual({
    kind: 'failed',
    message: 'The sample document could not be loaded. Please try again.',
  });
});

it('passes a rejected upload back without sealing a batch', async () => {
  stubFetch({ ok: true, blob: async () => new Blob(['%PDF']) });
  vi.mocked(uploadDocument).mockResolvedValueOnce({
    kind: 'rejected',
    reason: 'Storage limit reached',
  });

  const outcome = await uploadSampleDocument();

  expect(outcome).toEqual({
    kind: 'rejected',
    reason: 'Storage limit reached',
  });
  expect(sealBatch).not.toHaveBeenCalled();
  expect(announceBatchActivity).not.toHaveBeenCalled();
});
