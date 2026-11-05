import { afterEach, expect, it, vi } from 'vitest';
import api from './api';
import {
  deleteAdminAccount,
  forceCancelJob,
  jobQueuePlace,
  isActiveJob,
  jobStage,
  listAdminJobs,
  retryJob,
  type AdminJob,
} from './admin';

vi.mock('./api', () => ({
  default: { get: vi.fn(), delete: vi.fn(), post: vi.fn() },
}));

afterEach(() => {
  vi.clearAllMocks();
});

function job(overrides: Partial<AdminJob> = {}): AdminJob {
  return {
    id: 'j1',
    document_id: 'd1',
    filename: 'notes.pdf',
    owner_id: 'u1',
    owner_email: 'pat@example.com',
    owner_name: 'Pat Lee',
    status: 'queued',
    current_stage: 'queued',
    queue_position: 1,
    pages_processed: 0,
    total_pages: 0,
    error_message: null,
    created_at: '2026-01-01T00:00:00',
    is_stuck: false,
    can_cancel: true,
    can_retry: false,
    canceled_by_id: null,
    cancel_reason: null,
    retry_of_job_id: null,
    ...overrides,
  };
}

it('loads every processing job', async () => {
  const jobs = [job()];
  vi.mocked(api.get).mockResolvedValue({ data: jobs });
  await expect(listAdminJobs()).resolves.toEqual(jobs);
  expect(api.get).toHaveBeenCalledWith('/api/v1/admin/jobs');
});

it('shows running for a job that is processing', () => {
  expect(
    jobQueuePlace(job({ status: 'processing', queue_position: null }))
  ).toBe('Running');
});

it('shows queue place for a queued job', () => {
  expect(jobQueuePlace(job({ queue_position: 2 }))).toBe('2');
});

it('capitalizes the current stage', () => {
  expect(jobStage(job({ current_stage: 'parsing' }))).toBe('Parsing');
});

it('deletes an account by id', async () => {
  vi.mocked(api.delete).mockResolvedValue({});
  await deleteAdminAccount('user-1');
  expect(api.delete).toHaveBeenCalledWith('/api/v1/admin/accounts/user-1');
});

it('force-cancels a job with a trimmed reason', async () => {
  vi.mocked(api.post).mockResolvedValue({ data: { status: 'canceled' } });
  await expect(forceCancelJob('j1', '  Stuck  ')).resolves.toEqual({
    status: 'canceled',
  });
  expect(api.post).toHaveBeenCalledWith('/api/v1/admin/jobs/j1/cancel', {
    reason: 'Stuck',
  });
});

it('force-cancels a job without a reason', async () => {
  vi.mocked(api.post).mockResolvedValue({ data: {} });
  await forceCancelJob('j1', '   ');
  expect(api.post).toHaveBeenCalledWith('/api/v1/admin/jobs/j1/cancel', {});
});

it('retries a job', async () => {
  vi.mocked(api.post).mockResolvedValue({ data: { new_job_id: 'j2' } });
  await expect(retryJob('j1')).resolves.toEqual({ new_job_id: 'j2' });
  expect(api.post).toHaveBeenCalledWith('/api/v1/admin/jobs/j1/retry');
});

it('treats queued and processing jobs as active', () => {
  expect(isActiveJob(job({ status: 'queued' }))).toBe(true);
  expect(isActiveJob(job({ status: 'processing' }))).toBe(true);
  expect(isActiveJob(job({ status: 'failed' }))).toBe(false);
  expect(isActiveJob(job({ status: 'completed' }))).toBe(false);
  expect(isActiveJob(job({ status: 'canceled' }))).toBe(false);
});
