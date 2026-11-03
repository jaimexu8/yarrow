/**
 * US-60 Batch completion notifications: talking to the server, remembering
 * what was already shown, and the browser's Notification API.
 *
 * The server decides when a batch has finished and hands each finished batch
 * out once (it marks it notified in the same request). This module adds a
 * client-side guard on top, so a batch id already shown in this browser is
 * never shown again even if a response were somehow repeated.
 */
import api from './api';

/** One finished batch; matches BatchNotification on the server. */
export type BatchNotification = {
  batch_id: string;
  total: number;
  completed: number;
  failed: number;
  canceled: number;
  created_at: string | null;
  completed_at: string | null;
};

export type BatchNotifications = {
  notifications: BatchNotification[];
  /** Unannounced batches still being processed; 0 means stop checking. */
  pending: number;
};

/**
 * Claim this user's batches that finished and were not announced yet. Each is
 * leased to this tab for a minute so other tabs skip it; acknowledge it with
 * ``ackBatchNotifications`` once shown, or it is offered again later.
 */
export async function claimBatchNotifications(): Promise<BatchNotifications> {
  const res = await api.post<BatchNotifications>(
    '/api/v1/documents/batches/notifications/claim'
  );
  return res.data;
}

/** These notifications were shown: the server never offers them again. */
export async function ackBatchNotifications(batchIds: string[]): Promise<void> {
  if (batchIds.length === 0) return;
  await api.post('/api/v1/documents/batches/notifications/ack', {
    batch_ids: batchIds,
  });
}

/**
 * Tell the server every file of this run has been sent (US-60). Only a sealed
 * batch is announced, so a run whose first files finished before the last
 * one was sent is not reported early. Safe to call twice.
 */
export async function sealBatch(batchId: string): Promise<void> {
  await api.post(`/api/v1/documents/batches/${batchId}/seal`);
}

/** e.g. "All 5 files finished: 4 completed, 1 failed." */
export function completionSummary(n: BatchNotification): string {
  const files = n.total === 1 ? 'file' : 'files';
  const head =
    n.total === 1 ? 'Your file finished' : `All ${n.total} ${files} finished`;
  const parts: string[] = [];
  if (n.completed) parts.push(`${n.completed} completed`);
  if (n.failed) parts.push(`${n.failed} failed`);
  if (n.canceled) parts.push(`${n.canceled} canceled`);
  return parts.length ? `${head}: ${parts.join(', ')}.` : `${head}.`;
}

/** Whether every file in the batch was processed successfully. */
export function allSucceeded(n: BatchNotification): boolean {
  return n.completed === n.total;
}

// ---------------------------------------------------------------------------
// Client-side guard against showing one batch twice.
// ---------------------------------------------------------------------------
export const NOTIFIED_STORAGE_KEY = 'yarrow.batches.notified';
const MAX_REMEMBERED = 50;
// Used when storage is unavailable (private mode, blocked site data).
const notifiedInMemory = new Set<string>();

function readNotified(): string[] {
  try {
    const raw = localStorage.getItem(NOTIFIED_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((id): id is string => typeof id === 'string')
      : [];
  } catch {
    return [];
  }
}

export function wasNotified(batchId: string): boolean {
  return notifiedInMemory.has(batchId) || readNotified().includes(batchId);
}

export function rememberNotified(batchId: string): void {
  notifiedInMemory.add(batchId);
  try {
    const ids = readNotified().filter((id) => id !== batchId);
    ids.push(batchId);
    localStorage.setItem(
      NOTIFIED_STORAGE_KEY,
      JSON.stringify(ids.slice(-MAX_REMEMBERED))
    );
  } catch {
    // Remembered in memory only.
  }
}

/** For tests: forget the in-memory guard. */
export function resetNotifiedForTests(): void {
  notifiedInMemory.clear();
}

// ---------------------------------------------------------------------------
// A nudge to check again, e.g. right after a run was sealed.
// ---------------------------------------------------------------------------
const activityListeners = new Set<() => void>();

/** Called by the Upload page when a run has started or finished. */
export function announceBatchActivity(): void {
  activityListeners.forEach((listener) => listener());
}

/** Subscribe to ``announceBatchActivity``; returns the unsubscribe. */
export function onBatchActivity(listener: () => void): () => void {
  activityListeners.add(listener);
  return () => {
    activityListeners.delete(listener);
  };
}

// ---------------------------------------------------------------------------
// Browser notifications (optional; only after the user allows them).
// ---------------------------------------------------------------------------
export type BrowserPermission = NotificationPermission | 'unsupported';

export function browserNotificationPermission(): BrowserPermission {
  if (typeof window === 'undefined' || !('Notification' in window)) {
    return 'unsupported';
  }
  return Notification.permission;
}

/** Ask the browser for permission. Only ever call this from a user's click. */
export async function requestBrowserNotifications(): Promise<BrowserPermission> {
  if (browserNotificationPermission() === 'unsupported') return 'unsupported';
  try {
    return await Notification.requestPermission();
  } catch {
    return browserNotificationPermission();
  }
}

/**
 * Show a system notification for a finished batch, if the user allowed them.
 * The tag makes the browser replace rather than stack a duplicate. Returns
 * whether one was shown.
 */
export function showBrowserNotification(
  n: BatchNotification,
  onClick?: () => void
): boolean {
  if (browserNotificationPermission() !== 'granted') return false;
  try {
    const notification = new Notification('Yarrow: upload finished', {
      body: completionSummary(n),
      tag: `yarrow-batch-${n.batch_id}`,
    });
    notification.onclick = () => {
      window.focus();
      onClick?.();
      notification.close();
    };
    return true;
  } catch {
    // Some browsers (e.g. Android Chrome) only allow service-worker
    // notifications; the in-app notification is still shown.
    return false;
  }
}
