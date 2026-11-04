'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { CircleCheck, TriangleAlert, X } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import {
  ackBatchNotifications,
  allSucceeded,
  claimBatchNotifications,
  completionSummary,
  onBatchActivity,
  rememberNotified,
  showBrowserNotification,
  wasNotified,
  type BatchNotification,
} from '@/lib/batchNotifications';
import { BrowserNotificationsOffer } from './BrowserNotificationsOffer';

/** How often to check while an unannounced batch is still running. */
export const ACTIVE_POLL_MS = 10_000;
/** After a failed check, try again a little later. */
export const RETRY_POLL_MS = 30_000;

/**
 * US-60: tells the signed-in user when an upload batch has finished, on
 * whatever page they are on.
 *
 * Mounted once in the dashboard layout. It checks the server when it mounts,
 * when a run finishes uploading in this tab, and when the tab gets focus
 * again; then every ``ACTIVE_POLL_MS`` only while the server says a
 * batch is still running (or sent but not sealed, or leased by another tab
 * that has not acknowledged it). With nothing pending it makes no requests.
 * Each notification is acknowledged after it is shown, so a lost reply or a
 * closed tab means it is offered again, not lost.
 * It never blocks the page: the notifications sit in a corner, are announced
 * politely, and are dismissed with one click.
 */
export function BatchCompletionNotifier() {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const router = useRouter();
  // Kept with the user they were for, so nothing of a previous user's
  // session is shown after a different sign-in.
  const [shown, setShown] = useState<{
    forUser: string | null;
    items: BatchNotification[];
  }>({ forUser: null, items: [] });

  useEffect(() => {
    // Signed out: no checks (and the cleanup below stopped any running ones).
    if (!userId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;
    let again = false;

    async function check() {
      if (!alive) return;
      if (inFlight) {
        // Something changed mid-request; look once more when it returns.
        again = true;
        return;
      }
      inFlight = true;
      clearTimeout(timer);
      let next: number | null = null;
      try {
        const { notifications, pending } = await claimBatchNotifications();
        if (!alive) return;
        // The server hands each batch out once; this guards the client too.
        const fresh = notifications.filter((n) => !wasNotified(n.batch_id));
        fresh.forEach((n) => {
          rememberNotified(n.batch_id);
          showBrowserNotification(n, () => router.push('/dashboard'));
        });
        if (fresh.length) {
          setShown((current) => ({
            forUser: userId,
            items: [
              ...(current.forUser === userId ? current.items : []),
              ...fresh,
            ],
          }));
        }
        // Shown (or shown before, in this browser): tell the server, so no
        // tab is offered it again. If this is lost, the lease runs out, the
        // batch comes back, the guard above hides it and it is acked then.
        if (notifications.length) {
          ackBatchNotifications(notifications.map((n) => n.batch_id)).catch(
            () => {}
          );
        }
        if (pending > 0) next = ACTIVE_POLL_MS;
      } catch {
        if (!alive) return;
        next = RETRY_POLL_MS;
      } finally {
        inFlight = false;
      }
      if (again) {
        again = false;
        check();
      } else if (next !== null) {
        timer = setTimeout(check, next);
      }
    }

    function onVisible() {
      if (document.visibilityState === 'visible') check();
    }

    check();
    const stopListening = onBatchActivity(check);
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      alive = false;
      clearTimeout(timer);
      stopListening();
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [userId, router]);

  const visible = userId && shown.forUser === userId ? shown.items : [];

  function dismiss(batchId: string) {
    setShown((current) => ({
      ...current,
      items: current.items.filter((n) => n.batch_id !== batchId),
    }));
  }

  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex flex-col items-end gap-3 sm:left-auto sm:w-96"
    >
      {visible.map((n) => (
        <CompletionToast
          key={n.batch_id}
          notification={n}
          onDismiss={() => dismiss(n.batch_id)}
        />
      ))}
    </div>
  );
}

function CompletionToast({
  notification,
  onDismiss,
}: {
  notification: BatchNotification;
  onDismiss: () => void;
}) {
  const ok = allSucceeded(notification);
  const Icon = ok ? CircleCheck : TriangleAlert;
  return (
    <div
      role="status"
      aria-labelledby={`batch-done-${notification.batch_id}`}
      className="pointer-events-auto w-full rounded-xl border border-slate-200 bg-white p-4 shadow-lg"
    >
      <div className="flex items-start gap-3">
        <Icon
          aria-hidden="true"
          className={ok ? 'size-5 text-emerald-600' : 'size-5 text-amber-500'}
        />
        <div className="min-w-0 flex-1 space-y-1">
          <p
            id={`batch-done-${notification.batch_id}`}
            className="font-semibold text-slate-900"
          >
            Upload finished
          </p>
          <p className="text-sm text-slate-600">
            {completionSummary(notification)}
          </p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1">
            <Link
              href="/dashboard"
              onClick={onDismiss}
              className="text-sm font-medium text-slate-900 underline underline-offset-4 hover:text-slate-600"
            >
              Review documents
            </Link>
            <BrowserNotificationsOffer
              className="text-sm text-slate-600"
              label="Also notify me in the browser"
            />
          </div>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          <X aria-hidden="true" className="size-4" />
          <span className="sr-only">Dismiss notification</span>
        </button>
      </div>
    </div>
  );
}
