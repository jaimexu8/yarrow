import api from './api';

export type CloudConnection = {
  id: string;
  provider: string;
  account_email: string | null;
  account_name: string | null;
  last_synced_at: string | null;
  created_at: string | null;
};

export type SyncStatus = {
  provider: string;
  connected: boolean;
  account_email: string | null;
  last_synced_at: string | null;
  is_syncing: boolean;
};

export type SyncResponse = {
  provider: string;
  imported_count: number;
  files: string[];
  message: string;
};

export async function getCloudAuthUrl(
  provider: string,
  redirectUri?: string
): Promise<string> {
  const params = redirectUri ? { redirect_uri: redirectUri } : {};
  const res = await api.get<{ authorization_url: string }>(
    `/api/v1/integrations/oauth/${provider}/authorize`,
    { params }
  );
  return res.data.authorization_url;
}

export async function connectCloudAccount(
  provider: string,
  code: string,
  redirectUri?: string
): Promise<CloudConnection> {
  const res = await api.post<CloudConnection>(
    `/api/v1/integrations/oauth/${provider}/callback`,
    {
      provider,
      code,
      redirect_uri: redirectUri,
    }
  );
  return res.data;
}

export async function listCloudConnections(): Promise<CloudConnection[]> {
  const res = await api.get<CloudConnection[]>(
    '/api/v1/integrations/connections'
  );
  return res.data;
}

export async function disconnectCloudAccount(provider: string): Promise<void> {
  await api.delete(`/api/v1/integrations/connections/${provider}`);
}

export async function getCloudSyncStatus(): Promise<SyncStatus[]> {
  const res = await api.get<SyncStatus[]>('/api/v1/integrations/status');
  return res.data;
}

export async function syncCloudProvider(
  provider: string
): Promise<SyncResponse> {
  const res = await api.post<SyncResponse>(
    `/api/v1/integrations/${provider}/sync`
  );
  return res.data;
}

export async function syncAllCloudProviders(): Promise<SyncResponse[]> {
  const res = await api.post<SyncResponse[]>('/api/v1/integrations/sync');
  return res.data;
}
