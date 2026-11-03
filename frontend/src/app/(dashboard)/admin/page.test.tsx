import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import type { AdminJob, AdminStats } from '@/lib/admin';

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ user: { is_admin: true } }),
}));

vi.mock('@/lib/admin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/admin')>();
  return {
    ...actual,
    getAdminStats: vi.fn(),
    listAccounts: vi.fn(),
    listAdminJobs: vi.fn(),
    setAccountActive: vi.fn(),
  };
});

import AdminPage from './page';
import { getAdminStats, listAccounts, listAdminJobs } from '@/lib/admin';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const EMPTY_STATS: AdminStats = {
  user_count: 2,
  document_count: 3,
  storage_used_bytes: 0,
  jobs_by_status: {
    queued: 1,
    processing: 1,
    completed: 0,
    failed: 1,
    canceled: 0,
  },
};

function job(overrides: Partial<AdminJob> = {}): AdminJob {
  return {
    id: 'j1',
    document_id: 'd1',
    filename: 'notes.pdf',
    owner_id: 'u2',
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

async function renderAdmin(jobs: AdminJob[]) {
  vi.mocked(getAdminStats).mockResolvedValue(EMPTY_STATS);
  vi.mocked(listAccounts).mockResolvedValue([]);
  vi.mocked(listAdminJobs).mockResolvedValue(jobs);
  render(<AdminPage />);
  await waitFor(() => {
    expect(screen.getByRole('heading', { name: 'Jobs' })).toBeTruthy();
  });
}

it('shows jobs from other users with queue place', async () => {
  await renderAdmin([job()]);
  expect(screen.getByText('Pat Lee')).toBeTruthy();
  expect(screen.getByText('pat@example.com')).toBeTruthy();
  expect(screen.getByText('notes.pdf')).toBeTruthy();
  expect(screen.getByRole('cell', { name: '1' })).toBeTruthy();
});

it('shows queue place and stage for a running job', async () => {
  await renderAdmin([
    job({
      id: 'j2',
      status: 'processing',
      current_stage: 'parsing',
      queue_position: null,
    }),
  ]);
  expect(screen.getByText('Running')).toBeTruthy();
  expect(screen.getByText('Parsing')).toBeTruthy();
});

it('shows why a failed job failed', async () => {
  await renderAdmin([
    job({
      id: 'j3',
      status: 'failed',
      current_stage: 'parsing',
      queue_position: null,
      error_message:
        'The file was uploaded, but processing could not be started. Please try again later.',
    }),
  ]);
  expect(
    screen.getByText(
      'The file was uploaded, but processing could not be started. Please try again later.'
    )
  ).toBeTruthy();
});
