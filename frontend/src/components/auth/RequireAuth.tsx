'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { Spinner } from '@/components/ui/Spinner';
import { useAuth } from '@/context/AuthContext';

/**
 * Wraps pages that need a signed-in user (US-2): anyone else is sent to
 * /login, which brings them back here afterwards via ?next=.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === 'unauthenticated') {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [status, pathname, router]);

  if (status !== 'authenticated') {
    // Never render protected content, even briefly, before the check.
    return (
      <div className="flex min-h-dvh items-center justify-center text-slate-500">
        <Spinner label="Checking your session" />
      </div>
    );
  }
  return <>{children}</>;
}
