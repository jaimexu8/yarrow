/** US-79: how the upload client reports a duplicate and asks for a copy. */
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import api from './api';
import { uploadDocument } from './documents';

vi.mock('./api', () => ({
  default: { post: vi.fn() },
}));

afterEach(() => {
  vi.clearAllMocks();
});

const file = () =>
  new File(['%PDF-1.4'], 'copy.pdf', { type: 'application/pdf' });

const duplicateRejection = {
  filename: 'copy.pdf',
  reason: 'You already have an identical document in your library: a.pdf.',
  retryable: false,
  duplicate_of: {
    document_id: 'doc-1',
    filename: 'a.pdf',
    status: 'completed',
  },
};

/** What axios throws for the server's all-rejected 400. */
function badRequest(detail: unknown) {
  const headers = new AxiosHeaders();
  return new AxiosError('Bad Request', 'ERR_BAD_REQUEST', { headers }, null, {
    status: 400,
    statusText: 'Bad Request',
    headers: {},
    config: { headers },
    data: { detail },
  });
}

describe('uploadDocument duplicates', () => {
  it('reports a 400 with duplicate_of as a duplicate, naming my document', async () => {
    vi.mocked(api.post).mockRejectedValueOnce(badRequest([duplicateRejection]));

    const outcome = await uploadDocument(file(), 'upload-1', () => {}, 'b-1');

    expect(outcome).toEqual({
      kind: 'duplicate',
      reason: duplicateRejection.reason,
      duplicateOf: {
        documentId: 'doc-1',
        filename: 'a.pdf',
        status: 'completed',
      },
    });
  });

  it('reports a duplicate inside a 200 response too', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({
      data: { accepted: [], rejected: [duplicateRejection] },
    });

    const outcome = await uploadDocument(file(), 'upload-1', () => {});

    expect(outcome.kind).toBe('duplicate');
  });

  it('keeps other rejections as plain rejections', async () => {
    vi.mocked(api.post).mockRejectedValueOnce(
      badRequest([
        { filename: 'copy.pdf', reason: 'File is empty', duplicate_of: null },
      ])
    );

    const outcome = await uploadDocument(file(), 'upload-1', () => {});

    expect(outcome).toEqual({ kind: 'rejected', reason: 'File is empty' });
  });

  it('does not ask for a copy unless told to', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({
      data: { accepted: [{ document_id: 'doc-2', filename: 'copy.pdf' }] },
    });

    await uploadDocument(file(), 'upload-1', () => {}, 'b-1');

    const form = vi.mocked(api.post).mock.calls[0][1] as FormData;
    expect(form.has('allow_duplicate')).toBe(false);
  });

  it('Upload anyway sends allow_duplicate with the same upload id and batch', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({
      data: { accepted: [{ document_id: 'doc-2', filename: 'copy.pdf' }] },
    });

    const outcome = await uploadDocument(
      file(),
      'upload-1',
      () => {},
      'b-1',
      true
    );

    expect(outcome.kind).toBe('accepted');
    const form = vi.mocked(api.post).mock.calls[0][1] as FormData;
    expect(form.get('allow_duplicate')).toBe('true');
    expect(form.get('client_upload_ids')).toBe('upload-1');
    expect(form.get('batch_id')).toBe('b-1');
  });
});
