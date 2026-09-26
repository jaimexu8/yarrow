'use client';

import Link from 'next/link';
import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { useAuth } from '@/context/AuthContext';

/**
 * Top bar for signed-in pages: the app name, who is signed in, and logout
 * (US-19). Rendered by the (dashboard) layout, so every protected page has it.
 */
export default function Navbar() {
  const { user, logout } = useAuth();
  const [signingOut, setSigningOut] = useState(false);

  async function handleLogout() {
    setSigningOut(true);
    await logout();
  }

  return (
    <header className="border-b border-slate-200 bg-white">
      <nav
        aria-label="Main"
        className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3"
      >
        <Link
          href="/dashboard"
          className="inline-flex items-center gap-2 rounded text-lg font-semibold tracking-tight text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-slate-900"
        >
          <span
            aria-hidden="true"
            className="size-2.5 rounded-full bg-amber-400"
          />
          Yarrow
        </Link>
        <div className="flex min-w-0 items-center gap-4">
          {user && (
            <span className="hidden truncate text-sm text-slate-600 sm:inline">
              {user.name || user.email}
            </span>
          )}
          <Button
            variant="secondary"
            className="w-auto px-3 py-1.5"
            onClick={handleLogout}
            loading={signingOut}
          >
            Log out
          </Button>
        </div>
      </nav>
    </header>
  );
}
