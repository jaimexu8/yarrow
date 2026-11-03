import { afterEach, expect, it, vi } from 'vitest';
import api from './api';
import {
  deleteAdminAccount,
  jobQueuePlace,
  jobStage,
  listAdminJobs,
  type AdminJob,
} from './admin';

vi.mock('./api', () => ({ default: { get: vi.fn(), delete: vi.fn() } }));

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
