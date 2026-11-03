import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import type { AdminAccount, AdminStats } from '@/lib/admin';

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'admin-1', is_admin: true } }),
}));

vi.mock('@/lib/admin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/admin')>();
  return {
    ...actual,
    getAdminStats: vi.fn(),
    listAccounts: vi.fn(),
    deleteAdminAccount: vi.fn(),
    setAccountActive: vi.fn(),
  };
});

import AdminPage from './page';
import { deleteAdminAccount, getAdminStats, listAccounts } from '@/lib/admin';

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
  document_count: 1,
  storage_used_bytes: 0,
  jobs_by_status: {
    queued: 0,
    processing: 0,
    completed: 0,
    failed: 0,
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

async function renderAdmin() {
  vi.mocked(getAdminStats).mockResolvedValue(STATS);
  vi.mocked(listAccounts).mockResolvedValue(ACCOUNTS);
  render(<AdminPage />);
  await waitFor(() => {
    expect(screen.getByText('Pat Lee')).toBeTruthy();
  });
}

it('deletes another account after confirm', async () => {
  vi.mocked(deleteAdminAccount).mockResolvedValue();
  vi.mocked(getAdminStats)
    .mockResolvedValueOnce(STATS)
    .mockResolvedValue({ ...STATS, user_count: 1, document_count: 0 });
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
