import api from './api';

export type PageStatus =
  'queued' | 'processing' | 'completed' | 'failed' | 'canceled';

export type JobStage =
  | 'queued'
  | 'downloading'
  | 'preparing'
  | 'parsing'
  | 'saving'
  | 'finished'
  | 'failed'
  | 'canceled';

export type PageProgress = {
  page_number: number;
  status: PageStatus | null;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type DocumentProgress = {
  document_id: string;
  document_status: string | null;
  job_id: string | null;
  job_status: string | null;
  current_stage: string | null;
  pages_total: number;
  pages_completed: number;
  pages_failed: number;
  progress_percent: number;
  updated_at: string | null;
  pages: PageProgress[];
};

export const PROGRESS_POLL_INTERVAL_MS = 2000;

export const STAGE_LABELS: Record<JobStage, string> = {
  queued: 'Waiting in the queue',
  downloading: 'Fetching the file',
  preparing: 'Preparing pages',
  parsing: 'Reading pages',
  saving: 'Saving results',
  finished: 'Finished',
  failed: 'Failed',
  canceled: 'Canceled',
};

export function stageLabel(stage: string | null): string {
  return stage && stage in STAGE_LABELS
    ? STAGE_LABELS[stage as JobStage]
    : 'Processing';
}

export function isJobFinished(progress: DocumentProgress | null): boolean {
  return (
    progress?.job_status === 'completed' ||
    progress?.job_status === 'failed' ||
    progress?.job_status === 'canceled'
  );
}

export async function getDocumentProgress(
  documentId: string
): Promise<DocumentProgress> {
  const res = await api.get<DocumentProgress>(
    `/api/v1/documents/${documentId}/progress`
  );
  return res.data;
}
