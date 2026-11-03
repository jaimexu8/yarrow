/** US-60: claiming finished batches, summarizing them, and not repeating. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import api from './api';
import {
  NOTIFIED_STORAGE_KEY,
  ackBatchNotifications,
  announceBatchActivity,
  browserNotificationPermission,
  claimBatchNotifications,
  completionSummary,
  onBatchActivity,
  rememberNotified,
  requestBrowserNotifications,
  resetNotifiedForTests,
  sealBatch,
  showBrowserNotification,
  wasNotified,
  type BatchNotification,
} from './batchNotifications';

vi.mock('./api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

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

/** A stand-in for window.Notification with a settable permission. */
function installFakeNotification(permission: NotificationPermission) {
  const created: { title: string; options?: NotificationOptions }[] = [];
  class FakeNotification {
    static permission: NotificationPermission = permission;
    static requestPermission = vi.fn(async () => {
      FakeNotification.permission = 'granted';
      return 'granted' as NotificationPermission;
    });
    onclick: (() => void) | null = null;
    close = vi.fn();
    constructor(title: string, options?: NotificationOptions) {
      created.push({ title, options });
    }
  }
  vi.stubGlobal('Notification', FakeNotification);
  return { created, FakeNotification };
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  resetNotifiedForTests();
});

describe('claim, acknowledge, seal', () => {
  it('claims finished batches', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { notifications: [finished()], pending: 0 },
    });

    const result = await claimBatchNotifications();

    expect(api.post).toHaveBeenCalledWith(
      '/api/v1/documents/batches/notifications/claim'
    );
    expect(result.notifications).toHaveLength(1);
    expect(result.pending).toBe(0);
  });

  it('acknowledges shown notifications', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { acknowledged: [] } });
    await ackBatchNotifications(['a', 'b']);
    expect(api.post).toHaveBeenCalledWith(
      '/api/v1/documents/batches/notifications/ack',
      { batch_ids: ['a', 'b'] }
    );
  });

  it('sends no acknowledgement for nothing', async () => {
    await ackBatchNotifications([]);
    expect(api.post).not.toHaveBeenCalled();
  });

  it('seals a run', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: {} });
    await sealBatch('run-a');
    expect(api.post).toHaveBeenCalledWith(
      '/api/v1/documents/batches/run-a/seal'
    );
  });
});

describe('completionSummary', () => {
  it('summarizes a fully successful batch', () => {
    expect(completionSummary(finished())).toBe(
      'All 5 files finished: 5 completed.'
    );
  });

  it('summarizes mixed outcomes', () => {
    expect(
      completionSummary(finished({ completed: 3, failed: 1, canceled: 1 }))
    ).toBe('All 5 files finished: 3 completed, 1 failed, 1 canceled.');
  });

  it('reads naturally for a single file', () => {
    expect(
      completionSummary(finished({ total: 1, completed: 0, failed: 1 }))
    ).toBe('Your file finished: 1 failed.');
  });
});

describe('already-notified guard', () => {
  it('remembers a batch across page loads', () => {
    expect(wasNotified('batch-1')).toBe(false);
    rememberNotified('batch-1');
    resetNotifiedForTests(); // a reload: only storage is left
    expect(wasNotified('batch-1')).toBe(true);
    expect(JSON.parse(localStorage.getItem(NOTIFIED_STORAGE_KEY)!)).toEqual([
      'batch-1',
    ]);
  });

  it('keeps a bounded list', () => {
    for (let i = 0; i < 60; i++) rememberNotified(`batch-${i}`);
    const stored = JSON.parse(localStorage.getItem(NOTIFIED_STORAGE_KEY)!);
    expect(stored).toHaveLength(50);
    expect(stored.at(-1)).toBe('batch-59');
  });

  it('survives unreadable storage', () => {
    localStorage.setItem(NOTIFIED_STORAGE_KEY, 'not json');
    expect(wasNotified('batch-1')).toBe(false);
    rememberNotified('batch-1');
    expect(wasNotified('batch-1')).toBe(true);
  });
});

describe('batch activity', () => {
  it('nudges listeners until they unsubscribe', () => {
    const listener = vi.fn();
    const stop = onBatchActivity(listener);
    announceBatchActivity();
    stop();
    announceBatchActivity();
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe('browser notifications', () => {
  it('is unsupported without the Notification API', () => {
    // jsdom, like some mobile browsers, has no Notification at all.
    expect('Notification' in window).toBe(false);
    expect(browserNotificationPermission()).toBe('unsupported');
    expect(showBrowserNotification(finished())).toBe(false);
  });

  it('shows one only when permission is granted', () => {
    const { created } = installFakeNotification('granted');

    expect(showBrowserNotification(finished({ failed: 1, completed: 4 }))).toBe(
      true
    );
    expect(created).toEqual([
      {
        title: 'Yarrow: upload finished',
        options: {
          body: 'All 5 files finished: 4 completed, 1 failed.',
          tag: 'yarrow-batch-batch-1',
        },
      },
    ]);
  });

  it.each(['default', 'denied'] as const)(
    'shows none when permission is %s',
    (permission) => {
      const { created } = installFakeNotification(permission);
      expect(showBrowserNotification(finished())).toBe(false);
      expect(created).toEqual([]);
    }
  );

  it('asks for permission when requested', async () => {
    const { FakeNotification } = installFakeNotification('default');
    await expect(requestBrowserNotifications()).resolves.toBe('granted');
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
  });
});
