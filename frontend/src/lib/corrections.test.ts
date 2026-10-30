import { afterEach, expect, it, vi } from 'vitest';
import api from './api';
import {
  correctionErrorDetail,
  undoRegionCorrection,
  updateRegionText,
} from './corrections';

vi.mock('./api', () => ({ default: { put: vi.fn(), delete: vi.fn() } }));

afterEach(() => {
  vi.clearAllMocks();
});

it('puts the corrected text to the region text endpoint', async () => {
  vi.mocked(api.put).mockResolvedValue({ data: { changed: true } });
  await updateRegionText('doc-1', 'reg-1', 'fixed text');
  expect(api.put).toHaveBeenCalledWith(
    '/api/v1/documents/doc-1/regions/reg-1/text',
    { text: 'fixed text' }
  );
});

it('sends the reversion to the correction endpoint', async () => {
  vi.mocked(api.delete).mockResolvedValue({ data: { changed: true } });
  await undoRegionCorrection('doc-1', 'reg-1');
  expect(api.delete).toHaveBeenCalledWith(
    '/api/v1/documents/doc-1/regions/reg-1/correction'
  );
});

it('forwards the response body', async () => {
  vi.mocked(api.put).mockResolvedValue({
    data: { changed: false, document_updated_at: '2026-10-26T00:00:00Z' },
  });
  await expect(updateRegionText('d', 'r', 'same')).resolves.toEqual({
    changed: false,
    document_updated_at: '2026-10-26T00:00:00Z',
  });
});

it('reads the nested FastAPI detail message', () => {
  const error = {
    response: {
      data: {
        detail: {
          detail: 'Document has not finished processing',
          code: 'DOCUMENT_NOT_PROCESSED',
        },
      },
    },
  };
  expect(correctionErrorDetail(error)).toBe(
    'Document has not finished processing'
  );
});

it('reads a plain string detail', () => {
  const error = { response: { data: { detail: 'Document not found' } } };
  expect(correctionErrorDetail(error)).toBe('Document not found');
});

it('returns null for other errors', () => {
  expect(correctionErrorDetail(new Error('offline'))).toBeNull();
  expect(correctionErrorDetail(null)).toBeNull();
});
