import { describe, expect, it, vi } from 'vitest';
import api from './api';
import {
  connectCloudAccount,
  disconnectCloudAccount,
  getCloudAuthUrl,
  getCloudSyncStatus,
  listCloudConnections,
  syncAllCloudProviders,
  syncCloudProvider,
} from './integrations';

vi.mock('./api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('integrations client', () => {
  it('getCloudAuthUrl requests authorization url', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({
      data: {
        authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth',
      },
    });
    const url = await getCloudAuthUrl('google');
    expect(url).toBe('https://accounts.google.com/o/oauth2/v2/auth');
    expect(api.get).toHaveBeenCalledWith(
      '/api/v1/integrations/oauth/google/authorize',
      { params: {} }
    );
  });

  it('connectCloudAccount sends code and provider', async () => {
    const mockConn = {
      id: 'conn-1',
      provider: 'google',
      account_email: 'user@google.com',
      account_name: 'Test User',
      last_synced_at: null,
      created_at: null,
    };
    vi.mocked(api.post).mockResolvedValueOnce({ data: mockConn });
    const conn = await connectCloudAccount('google', 'test_code');
    expect(conn).toEqual(mockConn);
    expect(api.post).toHaveBeenCalledWith(
      '/api/v1/integrations/oauth/google/callback',
      {
        provider: 'google',
        code: 'test_code',
        redirect_uri: undefined,
      }
    );
  });

  it('listCloudConnections returns connections list', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: [] });
    const list = await listCloudConnections();
    expect(list).toEqual([]);
    expect(api.get).toHaveBeenCalledWith('/api/v1/integrations/connections');
  });

  it('disconnectCloudAccount calls delete', async () => {
    vi.mocked(api.delete).mockResolvedValueOnce({ data: null });
    await disconnectCloudAccount('dropbox');
    expect(api.delete).toHaveBeenCalledWith(
      '/api/v1/integrations/connections/dropbox'
    );
  });

  it('getCloudSyncStatus returns provider statuses', async () => {
    const statuses = [
      {
        provider: 'google',
        connected: true,
        account_email: 'user@google.com',
        last_synced_at: null,
        is_syncing: false,
      },
    ];
    vi.mocked(api.get).mockResolvedValueOnce({ data: statuses });
    const res = await getCloudSyncStatus();
    expect(res).toEqual(statuses);
  });

  it('syncCloudProvider calls sync endpoint', async () => {
    const syncRes = {
      provider: 'google',
      imported_count: 2,
      files: ['doc1.pdf', 'doc2.pdf'],
      message: 'Success',
    };
    vi.mocked(api.post).mockResolvedValueOnce({ data: syncRes });
    const res = await syncCloudProvider('google');
    expect(res).toEqual(syncRes);
    expect(api.post).toHaveBeenCalledWith('/api/v1/integrations/google/sync');
  });

  it('syncAllCloudProviders calls bulk sync endpoint', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: [] });
    const res = await syncAllCloudProviders();
    expect(res).toEqual([]);
    expect(api.post).toHaveBeenCalledWith('/api/v1/integrations/sync');
  });
});
