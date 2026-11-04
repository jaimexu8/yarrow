'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import {
  browserNotificationPermission,
  requestBrowserNotifications,
  type BrowserPermission,
} from '@/lib/batchNotifications';

/**
 * A small, optional offer to also get a system notification when an upload
 * finishes (US-60). Shown only while the browser has not been asked yet;
 * permission is requested only when the user clicks, never on page load.
 */
export function BrowserNotificationsOffer({
  className,
  label = 'Notify me in the browser when uploads finish',
}: {
  className?: string;
  label?: string;
}) {
  const [permission, setPermission] = useState<BrowserPermission>(() =>
    browserNotificationPermission()
  );

  if (permission !== 'default') return null;

  return (
    <Button
      variant="link"
      className={className ?? 'text-sm'}
      onClick={async () => setPermission(await requestBrowserNotifications())}
    >
      {label}
    </Button>
  );
}
