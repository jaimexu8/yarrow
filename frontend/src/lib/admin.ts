import api from './api';

export type AdminAccount = {
  id: string;
  name: string | null;
  email: string;
  created_at: string | null;
  is_admin: boolean;
  document_count: number;
};

export type JobStatusCounts = {
  queued: number;
  processing: number;
  completed: number;
  failed: number;
  canceled: number;
};

export type AdminStats = {
  user_count: number;
  document_count: number;
  storage_used_bytes: number;
  jobs_by_status: JobStatusCounts;
};

export async function listAccounts(): Promise<AdminAccount[]> {
  const res = await api.get<AdminAccount[]>('/api/v1/admin/accounts');
  return res.data;
}

export async function getAdminStats(): Promise<AdminStats> {
  const res = await api.get<AdminStats>('/api/v1/admin/stats');
  return res.data;
}
