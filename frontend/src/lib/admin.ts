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
  is_stuck: boolean;
  can_cancel: boolean;
  can_retry: boolean;
  canceled_by_id: string | null;
  cancel_reason: string | null;
  retry_of_job_id: string | null;
};

export async function listAdminJobs(): Promise<AdminJob[]> {
  const res = await api.get<AdminJob[]>('/api/v1/admin/jobs');
  return res.data;
}

export const JOBS_POLL_INTERVAL_MS = 4000;

export function isActiveJob(job: AdminJob): boolean {
  return job.status === 'queued' || job.status === 'processing';
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

export type AdminJobActionResult = {
  job_id: string;
  document_id: string;
  status: string;
  new_job_id: string | null;
  new_job_status: string | null;
  scope: 'incomplete' | 'all' | null;
  pages: number[] | null;
};

export async function forceCancelJob(
  jobId: string,
  reason?: string
): Promise<AdminJobActionResult> {
  const trimmed = reason?.trim();
  const res = await api.post<AdminJobActionResult>(
    `/api/v1/admin/jobs/${jobId}/cancel`,
    trimmed ? { reason: trimmed } : {}
  );
  return res.data;
}

export async function retryJob(jobId: string): Promise<AdminJobActionResult> {
  const res = await api.post<AdminJobActionResult>(
    `/api/v1/admin/jobs/${jobId}/retry`
  );
  return res.data;
}

export type NodeKind = 'worker' | 'inference';
export type NodeHealth = 'healthy' | 'degraded' | 'down' | 'unknown';

export type AdminNodeStatus = {
  id: string;
  kind: NodeKind;
  name: string;
  hostname: string;
  url: string | null;
  status: NodeHealth;
  latency_ms: number | null;
  last_checked_at: string | null;
  last_seen_at: string | null;
  last_error: string | null;
  details: Record<string, number | string | null>;
};

export type NodeStatusSummary = Record<NodeHealth, number>;

export type NodeStatusReport = {
  checked_at: string;
  overall_status: Exclude<NodeHealth, 'unknown'>;
  summary: NodeStatusSummary;
  nodes: AdminNodeStatus[];
};

export async function getNodeStatus(
  refresh = false
): Promise<NodeStatusReport> {
  const res = await api.get<NodeStatusReport>('/api/v1/admin/nodes', {
    params: refresh ? { refresh: true } : undefined,
  });
  return res.data;
}

export async function forgetNode(nodeId: string): Promise<void> {
  await api.delete(`/api/v1/admin/nodes/${nodeId}`);
}

export const NODE_HEALTH_LABELS: Record<NodeHealth, string> = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  down: 'Down',
  unknown: 'Unknown',
};

export const NODE_KIND_LABELS: Record<NodeKind, string> = {
  worker: 'Worker',
  inference: 'Inference server',
};

export function nodeLatency(node: AdminNodeStatus): string {
  return node.latency_ms == null ? '-' : `${node.latency_ms} ms`;
}

export function nodeDetails(node: AdminNodeStatus): string {
  const { details } = node;
  if (node.kind === 'worker') {
    const active = details.active_tasks;
    const concurrency = details.concurrency;
    if (typeof active !== 'number' || typeof concurrency !== 'number') {
      return '-';
    }
    return `${active} / ${concurrency} tasks`;
  }
  const model = [details.model_name, details.model_version]
    .filter((part): part is string => typeof part === 'string' && part !== '')
    .join(' ');
  return model || '-';
}

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['day', 86_400],
  ['hour', 3_600],
  ['minute', 60],
  ['second', 1],
];

export function relativeTime(value: Date, now: Date): string {
  const seconds = Math.round((now.getTime() - value.getTime()) / 1000);
  if (seconds < 10) return 'just now';
  const format = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
  for (const [unit, size] of RELATIVE_UNITS) {
    if (seconds >= size) {
      return format.format(-Math.floor(seconds / size), unit);
    }
  }
  return 'just now';
}
