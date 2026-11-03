/**
 * US-60: the completion notifier, mounted in the signed-in layout, announces
 * each finished batch once, on any page, without getting in the way.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import DashboardLayout from '@/app/(dashboard)/layout';
import {
  announceBatchActivity,
  resetNotifiedForTests,
  type BatchNotification,
  type BatchNotifications,
} from '@/lib/batchNotifications';
import type { User } from '@/lib/auth';
import {
  ACTIVE_POLL_MS,
  BatchCompletionNotifier,
  RETRY_POLL_MS,
} from './BatchCompletionNotifier';

const server = vi.hoisted(() => ({
  // Answers for the next claims, in order; then "nothing new".
  answers: [] as (BatchNotifications | Error)[],
  calls: 0,
  acked: [] as string[][],
}));

vi.mock('@/lib/batchNotifications', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/batchNotifications')>()),
  claimBatchNotifications: vi.fn(async () => {
    server.calls += 1;
    const next = server.answers.shift() ?? { notifications: [], pending: 0 };
    if (next instanceof Error) throw next;
    return next;
  }),
  ackBatchNotifications: vi.fn(async (ids: string[]) => {
    server.acked.push(ids);
  }),
}));

const auth = vi.hoisted(() => ({
  user: null as User | null,
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: auth.user,
    status: auth.user ? 'authenticated' : 'unauthenticated',
    isAuthenticated: Boolean(auth.user),
    logout: vi.fn(),
  }),
}));

const navigation = vi.hoisted(() => ({
  pathname: '/dashboard',
  router: { push: vi.fn(), replace: vi.fn() },
}));

vi.mock('next/navigation', () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => navigation.router,
}));

const SIGNED_IN: User = {
  id: 'user-1',
  email: 'user@example.com',
  name: 'Test User',
  is_admin: false,
  storage_used_bytes: 0,
};

function finished(
  overrides: Partial<BatchNotification> = {}
): BatchNotification {
  return {
    batch_id: 'batch-1',
    total: 5,
    completed: 5,
    failed: 0,
    canceled: 0,
    created_at: '2026-11-03T10:00:00',
    completed_at: '2026-11-03T10:05:00',
    ...overrides,
  };
}

function installFakeNotification(permission: NotificationPermission) {
  const created: { title: string; options?: NotificationOptions }[] = [];
  class FakeNotification {
    static permission = permission;
    static requestPermission = vi.fn(async () => 'granted' as const);
    onclick: (() => void) | null = null;
    close = vi.fn();
    constructor(title: string, options?: NotificationOptions) {
      created.push({ title, options });
    }
  }
  vi.stubGlobal('Notification', FakeNotification);
  return created;
}

/** Let pending promises and timers due within ``ms`` run. */
async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  auth.user = SIGNED_IN;
  navigation.pathname = '/dashboard';
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  server.answers = [];
  server.calls = 0;
  server.acked = [];
  localStorage.clear();
  resetNotifiedForTests();
});

describe('in-app completion notification', () => {
  it('summarizes a batch where every file succeeded', async () => {
    server.answers = [{ notifications: [finished()], pending: 0 }];
    render(<BatchCompletionNotifier />);
    await flush();

    const toast = screen.getByRole('status', { name: 'Upload finished' });
    expect(toast.textContent).toContain('All 5 files finished: 5 completed.');
    const link = screen.getByRole('link', { name: 'Review documents' });
    expect(link.getAttribute('href')).toBe('/dashboard');
  });

  it('summarizes mixed outcomes', async () => {
    server.answers = [
      {
        notifications: [finished({ completed: 2, failed: 2, canceled: 1 })],
        pending: 0,
      },
    ];
    render(<BatchCompletionNotifier />);
    await flush();

    expect(screen.getByRole('status').textContent).toContain(
      'All 5 files finished: 2 completed, 2 failed, 1 canceled.'
    );
  });

  it('is dismissible', async () => {
    server.answers = [{ notifications: [finished()], pending: 0 }];
    render(<BatchCompletionNotifier />);
    await flush();

    fireEvent.click(
      screen.getByRole('button', { name: 'Dismiss notification' })
    );
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('shows one notification per finished batch', async () => {
    server.answers = [
      {
        notifications: [
          finished({ batch_id: 'a' }),
          finished({ batch_id: 'b', total: 2, completed: 1, failed: 1 }),
        ],
        pending: 0,
      },
    ];
    render(<BatchCompletionNotifier />);
    await flush();

    expect(screen.getAllByRole('status')).toHaveLength(2);
  });
});

describe('no repeated notifications', () => {
  it('never shows the same batch twice, even if the server repeated it', async () => {
    const created = installFakeNotification('granted');
    server.answers = [
      { notifications: [finished()], pending: 1 },
      { notifications: [finished()], pending: 0 },
    ];
    render(<BatchCompletionNotifier />);
    await flush();
    await flush(ACTIVE_POLL_MS);

    expect(server.calls).toBe(2);
    expect(screen.getAllByRole('status')).toHaveLength(1);
    expect(created).toHaveLength(1);
  });

  it('does not bring a dismissed batch back after a reload', async () => {
    server.answers = [{ notifications: [finished()], pending: 0 }];
    const first = render(<BatchCompletionNotifier />);
    await flush();
    fireEvent.click(
      screen.getByRole('button', { name: 'Dismiss notification' })
    );
    first.unmount();

    resetNotifiedForTests(); // a fresh page: only localStorage remains
    server.answers = [{ notifications: [finished()], pending: 0 }];
    render(<BatchCompletionNotifier />);
    await flush();

    expect(screen.queryByRole('status')).toBeNull();
  });
});

describe('acknowledging delivery', () => {
  it('acknowledges a notification once it is shown', async () => {
    server.answers = [
      {
        notifications: [
          finished({ batch_id: 'a' }),
          finished({ batch_id: 'b' }),
        ],
        pending: 0,
      },
    ];
    render(<BatchCompletionNotifier />);
    await flush();

    expect(screen.getAllByRole('status')).toHaveLength(2);
    expect(server.acked).toEqual([['a', 'b']]);
  });

  it('acknowledges, without showing again, a batch re-offered after a lost ack', async () => {
    // Shown earlier in this browser (maybe another tab) but the ack was lost,
    // so the server offers it again once the lease runs out.
    const created = installFakeNotification('granted');
    localStorage.setItem('yarrow.batches.notified', JSON.stringify(['a']));
    server.answers = [
      { notifications: [finished({ batch_id: 'a' })], pending: 0 },
    ];
    render(<BatchCompletionNotifier />);
    await flush();

    expect(screen.queryByRole('status')).toBeNull();
    expect(created).toEqual([]);
    expect(server.acked).toEqual([['a']]);
  });

  it('shows a re-offered batch that never reached the screen', async () => {
    // Claimed by a tab that closed before showing it: no guard entry here.
    server.answers = [
      { notifications: [finished({ batch_id: 'a' })], pending: 0 },
    ];
    render(<BatchCompletionNotifier />);
    await flush();
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('keeps working when the acknowledgement fails', async () => {
    const { ackBatchNotifications } = await import('@/lib/batchNotifications');
    vi.mocked(ackBatchNotifications).mockRejectedValueOnce(
      new Error('offline')
    );
    server.answers = [{ notifications: [finished()], pending: 1 }];
    render(<BatchCompletionNotifier />);
    await flush();
    await flush(ACTIVE_POLL_MS);

    expect(screen.getAllByRole('status')).toHaveLength(1);
    expect(server.calls).toBe(2);
  });
});

describe('browser notification', () => {
  it('is shown when permission is granted', async () => {
    const created = installFakeNotification('granted');
    server.answers = [
      { notifications: [finished({ completed: 4, failed: 1 })], pending: 0 },
    ];
    render(<BatchCompletionNotifier />);
    await flush();

    expect(created).toEqual([
      {
        title: 'Yarrow: upload finished',
        options: {
          body: 'All 5 files finished: 4 completed, 1 failed.',
          tag: 'yarrow-batch-batch-1',
        },
      },
    ]);
    // The in-app one is shown as well.
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it.each(['default', 'denied'] as const)(
    'is not shown when permission is %s',
    async (permission) => {
      const created = installFakeNotification(permission);
      server.answers = [{ notifications: [finished()], pending: 0 }];
      render(<BatchCompletionNotifier />);
      await flush();

      expect(created).toEqual([]);
      expect(screen.getByRole('status')).toBeTruthy();
    }
  );

  it('is only requested when the user clicks the offer', async () => {
    installFakeNotification('default');
    server.answers = [{ notifications: [finished()], pending: 0 }];
    render(<BatchCompletionNotifier />);
    await flush();
    expect(Notification.requestPermission).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole('button', { name: 'Also notify me in the browser' })
    );
    await flush();

    expect(Notification.requestPermission).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole('button', { name: 'Also notify me in the browser' })
    ).toBeNull();
  });
});

describe('checking lightly', () => {
  it('keeps checking only while a batch is still running', async () => {
    server.answers = [
      { notifications: [], pending: 1 },
      { notifications: [finished()], pending: 0 },
    ];
    render(<BatchCompletionNotifier />);
    await flush();
    expect(server.calls).toBe(1);
    expect(screen.queryByRole('status')).toBeNull();

    await flush(ACTIVE_POLL_MS);
    expect(server.calls).toBe(2);
    expect(screen.getByRole('status')).toBeTruthy();

    // Nothing left running: no more requests.
    await flush(ACTIVE_POLL_MS * 10);
    expect(server.calls).toBe(2);
  });

  it('retries later after a failed check', async () => {
    server.answers = [new Error('offline')];
    render(<BatchCompletionNotifier />);
    await flush();
    await flush(RETRY_POLL_MS);
    expect(server.calls).toBe(2);
  });

  it('checks again as soon as a run is sealed', async () => {
    render(<BatchCompletionNotifier />);
    await flush();
    expect(server.calls).toBe(1);

    act(() => announceBatchActivity());
    await flush();
    expect(server.calls).toBe(2);
  });

  it('checks again when the tab gets focus', async () => {
    render(<BatchCompletionNotifier />);
    await flush();
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await flush();
    expect(server.calls).toBe(2);
  });

  it('stops checking once signed out', async () => {
    server.answers = [{ notifications: [finished()], pending: 1 }];
    const view = render(<BatchCompletionNotifier />);
    await flush();
    expect(screen.getByRole('status')).toBeTruthy();

    auth.user = null;
    view.rerender(<BatchCompletionNotifier />);
    await flush(ACTIVE_POLL_MS * 5);

    expect(server.calls).toBe(1);
    // And nothing of that session is left on screen.
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('makes no request without a signed-in user', async () => {
    auth.user = null;
    render(<BatchCompletionNotifier />);
    await flush(ACTIVE_POLL_MS * 3);
    expect(server.calls).toBe(0);
  });
});

describe('non-blocking, on every signed-in page', () => {
  it('lets the user keep working on another page while a batch runs', async () => {
    navigation.pathname = '/search';
    server.answers = [
      { notifications: [], pending: 1 },
      { notifications: [finished({ completed: 4, failed: 1 })], pending: 0 },
    ];
    const typed = vi.fn();
    render(
      <DashboardLayout>
        <h1>Search</h1>
        <input aria-label="Search documents" onChange={typed} />
      </DashboardLayout>
    );
    await flush();

    // The page renders and responds while the batch is still processing.
    const input = screen.getByRole('textbox', { name: 'Search documents' });
    fireEvent.change(input, { target: { value: 'invoice' } });
    expect(typed).toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();

    // The notification arrives on this (non-Upload) page, without a modal
    // and without taking focus away from what the user was doing.
    input.focus();
    await flush(ACTIVE_POLL_MS);
    expect(
      screen.getByRole('status', { name: 'Upload finished' })
    ).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Search' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(input);
  });
});
