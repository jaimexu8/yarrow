import api from './api';
import type { DocumentStatus } from './documents';

export type AdminAccount = {
  id: string;
  name: string | null;
  email: string;
  created_at: string | null;
  is_admin: boolean;
  document_count: number;
  is_active: boolean;
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

export async function setAccountStatus(
  accountId: string,
  status: { is_active?: boolean; is_admin?: boolean }
): Promise<void> {
  await api.patch(`/api/v1/admin/accounts/${accountId}/status`, {
    ...status,
  });
}

export async function getAdminStats(): Promise<AdminStats> {
  const res = await api.get<AdminStats>('/api/v1/admin/stats');
  return res.data;
}

export async function deleteAdminAccount(accountId: string): Promise<void> {
  await api.delete(`/api/v1/admin/accounts/${accountId}`);
}

export type AdminJob = {
  id: string;
  document_id: string;
  filename: string;
  owner_id: string;
  owner_email: string;
  owner_name: string | null;
  status: DocumentStatus | null;
  current_stage: string | null;
  queue_position: number | null;
  pages_processed: number | null;
  total_pages: number | null;
  error_message: string | null;
  created_at: string | null;
};

export async function listAdminJobs(): Promise<AdminJob[]> {
  const res = await api.get<AdminJob[]>('/api/v1/admin/jobs');
  return res.data;
}

export function jobQueuePlace(job: AdminJob): string {
  if (job.status === 'processing') return 'Running';
  if (job.queue_position != null) return String(job.queue_position);
  return '—';
}

export function jobStage(job: AdminJob): string {
  if (!job.current_stage) return '—';
  return job.current_stage.charAt(0).toUpperCase() + job.current_stage.slice(1);
}
