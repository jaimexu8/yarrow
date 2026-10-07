import { describe, expect, it, vi } from 'vitest';
import api from './api';
import {
  listDocumentShares,
  revokeDocumentShare,
  shareDocument,
  updateSharePermission,
} from './sharing';

vi.mock('./api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
  },
}));

describe('sharing client', () => {
  it('listDocumentShares fetches shares for document', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: [] });
    const shares = await listDocumentShares('doc-1');
    expect(shares).toEqual([]);
    expect(api.get).toHaveBeenCalledWith('/api/v1/documents/doc-1/shares');
  });

  it('shareDocument posts new share', async () => {
    const mockShare = {
      id: 'share-1',
      document_id: 'doc-1',
      shared_with_user_id: 'user-2',
      shared_with_email: 'user2@example.com',
      shared_with_name: 'User Two',
      permission: 'review' as const,
      created_at: null,
    };
    vi.mocked(api.post).mockResolvedValueOnce({ data: mockShare });
    const res = await shareDocument('doc-1', 'user2@example.com', 'review');
    expect(res).toEqual(mockShare);
    expect(api.post).toHaveBeenCalledWith('/api/v1/documents/doc-1/share', {
      email: 'user2@example.com',
      permission: 'review',
    });
  });

  it('revokeDocumentShare calls delete', async () => {
    vi.mocked(api.delete).mockResolvedValueOnce({ data: null });
    await revokeDocumentShare('doc-1', 'share-1');
    expect(api.delete).toHaveBeenCalledWith(
      '/api/v1/documents/doc-1/shares/share-1'
    );
  });

  it('updateSharePermission calls patch', async () => {
    const updated = {
      id: 'share-1',
      document_id: 'doc-1',
      shared_with_user_id: 'user-2',
      shared_with_email: 'user2@example.com',
      shared_with_name: null,
      permission: 'view' as const,
      created_at: null,
    };
    vi.mocked(api.patch).mockResolvedValueOnce({ data: updated });
    const res = await updateSharePermission('doc-1', 'share-1', 'view');
    expect(res).toEqual(updated);
    expect(api.patch).toHaveBeenCalledWith(
      '/api/v1/documents/doc-1/shares/share-1',
      { permission: 'view' }
    );
  });
});
