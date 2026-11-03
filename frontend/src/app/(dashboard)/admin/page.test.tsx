import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import type { AdminAccount, AdminJob, AdminStats } from '@/lib/admin';

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'admin-1', is_admin: true } }),
}));

vi.mock('@/lib/admin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/admin')>();
  return {
    ...actual,
    getAdminStats: vi.fn(),
    listAccounts: vi.fn(),
    listAdminJobs: vi.fn(),
    deleteAdminAccount: vi.fn(),
    setAccountActive: vi.fn(),
  };
});

import AdminPage from './page';
import {
  deleteAdminAccount,
  getAdminStats,
  listAccounts,
  listAdminJobs,
} from '@/lib/admin';

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (
    this: HTMLDialogElement
  ) {
    this.open = true;
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.open = false;
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const STATS: AdminStats = {
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

const ACCOUNTS: AdminAccount[] = [
  {
    id: 'admin-1',
    name: 'Aagam',
    email: 'admin@example.com',
    created_at: '2026-01-01T00:00:00',
    is_admin: true,
    document_count: 0,
    is_active: true,
  },
  {
    id: 'user-2',
    name: 'Pat Lee',
    email: 'pat@example.com',
    created_at: '2026-01-02T00:00:00',
    is_admin: false,
    document_count: 1,
    is_active: true,
  },
];

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

async function renderAdmin({
  jobs = [],
  accounts = ACCOUNTS,
}: {
  jobs?: AdminJob[];
  accounts?: AdminAccount[];
} = {}) {
  vi.mocked(getAdminStats).mockResolvedValue(STATS);
  vi.mocked(listAccounts).mockResolvedValue(accounts);
  vi.mocked(listAdminJobs).mockResolvedValue(jobs);
  render(<AdminPage />);
  await waitFor(() => {
    expect(screen.getByRole('heading', { name: 'Jobs' })).toBeTruthy();
  });
}

it('shows jobs from other users with queue place', async () => {
  await renderAdmin({ jobs: [job()], accounts: [] });
  expect(screen.getByText('Pat Lee')).toBeTruthy();
  expect(screen.getByText('pat@example.com')).toBeTruthy();
  expect(screen.getByText('notes.pdf')).toBeTruthy();
  expect(screen.getByRole('cell', { name: '1' })).toBeTruthy();
});

it('shows queue place and stage for a running job', async () => {
  await renderAdmin({
    jobs: [
      job({
        id: 'j2',
        status: 'processing',
        current_stage: 'parsing',
        queue_position: null,
      }),
    ],
    accounts: [],
  });
  expect(screen.getByText('Running')).toBeTruthy();
  expect(screen.getByText('Parsing')).toBeTruthy();
});

it('shows why a failed job failed', async () => {
  await renderAdmin({
    jobs: [
      job({
        id: 'j3',
        status: 'failed',
        current_stage: 'parsing',
        queue_position: null,
        error_message:
          'The file was uploaded, but processing could not be started. Please try again later.',
      }),
    ],
    accounts: [],
  });
  expect(
    screen.getByText(
      'The file was uploaded, but processing could not be started. Please try again later.'
    )
  ).toBeTruthy();
});

it('deletes another account after confirm', async () => {
  vi.mocked(deleteAdminAccount).mockResolvedValue();
  await renderAdmin();

  fireEvent.click(
    screen.getByRole('button', { name: 'More actions for Pat Lee' })
  );
  fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));

  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByText(/all of their documents/)).toBeTruthy();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

  await waitFor(() => {
    expect(deleteAdminAccount).toHaveBeenCalledWith('user-2');
  });
  await waitFor(() => {
    expect(screen.queryByText('Pat Lee')).toBeNull();
  });
});

it('does not offer delete on the signed-in admin', async () => {
  await renderAdmin();
  fireEvent.click(
    screen.getByRole('button', { name: 'More actions for Aagam' })
  );
  expect(screen.queryByRole('menuitem', { name: 'Delete' })).toBeNull();
});
