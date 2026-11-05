import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { AxiosError, AxiosHeaders } from 'axios';
import {
  JOBS_POLL_INTERVAL_MS,
  type AdminAccount,
  type AdminJob,
  type AdminStats,
} from '@/lib/admin';

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'admin-1', is_admin: true } }),
}));

vi.mock('@/components/admin/SystemStatusPanel', () => ({
  SystemStatusPanel: () => <section aria-label="System status" />,
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
    forceCancelJob: vi.fn(),
    retryJob: vi.fn(),
  };
});

import AdminPage from './page';
import {
  deleteAdminAccount,
  forceCancelJob,
  getAdminStats,
  listAccounts,
  listAdminJobs,
  retryJob,
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
    is_stuck: false,
    can_cancel: true,
    can_retry: false,
    canceled_by_id: null,
    cancel_reason: null,
    retry_of_job_id: null,
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

const FAILED = {
  id: 'j9',
  status: 'failed' as const,
  current_stage: 'failed',
  queue_position: null,
  can_cancel: false,
  can_retry: true,
};

it('flags a stuck job and offers force cancel only', async () => {
  await renderAdmin({
    jobs: [job({ status: 'processing', is_stuck: true })],
    accounts: [],
  });
  expect(screen.getByText('Stuck')).toBeTruthy();
  expect(
    screen.getByRole('button', { name: 'Force cancel notes.pdf' })
  ).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Retry notes.pdf' })).toBeNull();
});

it('offers no actions for a finished job', async () => {
  await renderAdmin({
    jobs: [job({ status: 'completed', can_cancel: false })],
    accounts: [],
  });
  expect(
    screen.queryByRole('button', { name: /Force cancel|Retry/ })
  ).toBeNull();
});

it('force-cancels a job with a reason and reloads the table', async () => {
  vi.mocked(forceCancelJob).mockResolvedValue({
    job_id: 'j1',
    document_id: 'd1',
    status: 'canceled',
    new_job_id: null,
    new_job_status: null,
    scope: null,
    pages: null,
  });
  await renderAdmin({ jobs: [job()], accounts: [] });
  vi.mocked(listAdminJobs).mockResolvedValue([
    job({ status: 'canceled', can_cancel: false }),
  ]);

  fireEvent.click(
    screen.getByRole('button', { name: 'Force cancel notes.pdf' })
  );
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByText('Force-cancel this job?')).toBeTruthy();
  expect(within(dialog).getByText('pat@example.com')).toBeTruthy();
  fireEvent.change(within(dialog).getByLabelText('Reason (optional)'), {
    target: { value: 'Stuck for hours' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Force cancel' }));

  await waitFor(() => {
    expect(forceCancelJob).toHaveBeenCalledWith('j1', 'Stuck for hours');
  });
  await waitFor(() => {
    expect(
      screen.queryByRole('button', { name: 'Force cancel notes.pdf' })
    ).toBeNull();
  });
  expect(listAdminJobs).toHaveBeenCalledTimes(2);
});

it('shows the API error inside the modal when retry fails', async () => {
  vi.mocked(retryJob).mockRejectedValue(new Error('Queue down'));
  await renderAdmin({ jobs: [job(FAILED)], accounts: [] });

  fireEvent.click(screen.getByRole('button', { name: 'Retry notes.pdf' }));
  const dialog = await screen.findByRole('dialog');
  expect(
    within(dialog).getByText(/The owner does not need to upload it again/)
  ).toBeTruthy();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }));

  await waitFor(() => {
    expect(within(dialog).getByRole('alert')).toBeTruthy();
  });
  expect(retryJob).toHaveBeenCalledWith('j9');
  expect(listAdminJobs).toHaveBeenCalledTimes(1);
});

it('disables both buttons while a retry is pending', async () => {
  vi.mocked(retryJob).mockReturnValue(new Promise(() => {}));
  await renderAdmin({ jobs: [job(FAILED)], accounts: [] });

  fireEvent.click(screen.getByRole('button', { name: 'Retry notes.pdf' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }));

  await waitFor(() => {
    expect(
      within(dialog)
        .getByRole('button', { name: 'Retry' })
        .hasAttribute('disabled')
    ).toBe(true);
  });
  expect(
    within(dialog)
      .getByRole('button', { name: 'Cancel' })
      .hasAttribute('disabled')
  ).toBe(true);
});

function conflict(detail: string, code: string) {
  const headers = new AxiosHeaders();
  return new AxiosError('Conflict', 'ERR_BAD_REQUEST', { headers }, null, {
    status: 409,
    statusText: 'Conflict',
    headers: {},
    config: { headers },
    data: { detail: { detail, code } },
  });
}

it('explains a refused force cancel and drops the stale button', async () => {
  vi.mocked(forceCancelJob).mockRejectedValue(
    conflict(
      'This job has already failed, so there is nothing left to cancel.',
      'JOB_NOT_CANCELABLE'
    )
  );
  await renderAdmin({
    jobs: [job({ status: 'processing', current_stage: 'preparing' })],
    accounts: [],
  });
  vi.mocked(listAdminJobs).mockResolvedValue([job(FAILED)]);

  fireEvent.click(
    screen.getByRole('button', { name: 'Force cancel notes.pdf' })
  );
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Force cancel' }));

  await waitFor(() => {
    expect(within(dialog).getByRole('alert').textContent).toBe(
      'This job has already failed, so there is nothing left to cancel.'
    );
  });
  await waitFor(() => {
    expect(
      screen.queryByRole('button', { name: 'Force cancel notes.pdf' })
    ).toBeNull();
  });
  expect(screen.getByRole('button', { name: 'Retry notes.pdf' })).toBeTruthy();
});

it('keeps polling jobs while one is active so its actions stay current', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    await renderAdmin({ jobs: [job()], accounts: [] });
    expect(
      screen.getByRole('button', { name: 'Force cancel notes.pdf' })
    ).toBeTruthy();
    vi.mocked(listAdminJobs).mockResolvedValue([job(FAILED)]);

    await vi.advanceTimersByTimeAsync(JOBS_POLL_INTERVAL_MS);

    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: 'Force cancel notes.pdf' })
      ).toBeNull();
    });
    expect(
      screen.getByRole('button', { name: 'Retry notes.pdf' })
    ).toBeTruthy();
    const calls = vi.mocked(listAdminJobs).mock.calls.length;

    await vi.advanceTimersByTimeAsync(JOBS_POLL_INTERVAL_MS * 2);
    expect(listAdminJobs).toHaveBeenCalledTimes(calls);
  } finally {
    vi.useRealTimers();
  }
});

it('keeps polling after a failed refresh', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    await renderAdmin({ jobs: [job()], accounts: [] });
    vi.mocked(listAdminJobs).mockRejectedValueOnce(new Error('offline'));

    await vi.advanceTimersByTimeAsync(JOBS_POLL_INTERVAL_MS);
    await waitFor(() => {
      expect(listAdminJobs).toHaveBeenCalledTimes(2);
    });
    vi.mocked(listAdminJobs).mockResolvedValue([job(FAILED)]);

    await vi.advanceTimersByTimeAsync(JOBS_POLL_INTERVAL_MS);
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: 'Retry notes.pdf' })
      ).toBeTruthy();
    });
  } finally {
    vi.useRealTimers();
  }
});

it('shows the system status panel', async () => {
  await renderAdmin();
  expect(screen.getByRole('region', { name: 'System status' })).toBeTruthy();
});
