'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { ChevronsUpDown, LogOut, Settings } from 'lucide-react';
import { Spinner } from '@/components/ui/Spinner';
import { useAuth } from '@/context/AuthContext';
import type { User } from '@/lib/auth';
import { cn } from '@/lib/cn';

/** Up to two letters from the name, or the first letter of the email. */
function initials(user: User): string {
  const words = (user.name ?? '').trim().split(/\s+/).filter(Boolean);
  if (words.length > 0) {
    return words
      .slice(0, 2)
      .map((word) => word[0])
      .join('')
      .toUpperCase();
  }
  return user.email.charAt(0).toUpperCase();
}

const ITEM_CLASS =
  'flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 hover:bg-slate-100 hover:text-slate-900 focus:bg-slate-100 focus:text-slate-900 focus:outline-none disabled:cursor-not-allowed disabled:text-slate-400';

/**
 * The signed-in user's name at the foot of the sidebar. Clicking it opens a
 * menu, above the button, with account settings and logout (US-19).
 *
 * Keyboard: Enter/Space/ArrowUp open it; arrows, Home and End move between
 * items; Escape closes it and returns focus to the button; Tab closes it.
 */
export function AccountMenu() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const menuId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const menuItems = useCallback(
    () =>
      Array.from(
        menuRef.current?.querySelectorAll<HTMLElement>(
          '[role="menuitem"]:not([disabled])'
        ) ?? []
      ),
    []
  );

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) buttonRef.current?.focus();
  }, []);

  // Focus the first item when the menu opens.
  useEffect(() => {
    if (open) menuItems()[0]?.focus();
  }, [open, menuItems]);

  // Clicking anywhere outside closes it.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) close(false);
    }
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open, close]);

  // Navigating (e.g. to settings) closes it.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  function onButtonKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
    }
  }

  function onMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const items = menuItems();
    const index = items.indexOf(document.activeElement as HTMLElement);
    const focusAt = (i: number) =>
      items[(i + items.length) % items.length]?.focus();

    switch (event.key) {
      case 'Escape':
        event.preventDefault();
        close(true);
        break;
      case 'Tab':
        close(false);
        break;
      case 'ArrowDown':
        event.preventDefault();
        focusAt(index + 1);
        break;
      case 'ArrowUp':
        event.preventDefault();
        focusAt(index - 1);
        break;
      case 'Home':
        event.preventDefault();
        focusAt(0);
        break;
      case 'End':
        event.preventDefault();
        focusAt(items.length - 1);
        break;
    }
  }

  async function handleLogout() {
    setSigningOut(true);
    await logout();
  }

  if (!user) return null;

  const displayName = user.name?.trim() || user.email;

  return (
    <div ref={containerRef} className="relative">
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label="Account"
          onKeyDown={onMenuKeyDown}
          className="absolute inset-x-0 bottom-full z-20 mb-2 rounded-xl border border-slate-200 bg-white p-1.5 shadow-lg shadow-slate-900/10"
        >
          <div className="border-b border-slate-100 px-2.5 pb-2.5 pt-1.5">
            {user.name && (
              <p className="truncate text-sm font-medium text-slate-900">
                {user.name}
              </p>
            )}
            <p className="truncate text-xs text-slate-500">{user.email}</p>
          </div>
          <div className="pt-1.5">
            <Link
              href="/settings"
              role="menuitem"
              tabIndex={-1}
              className={ITEM_CLASS}
            >
              <Settings aria-hidden="true" className="size-4 text-slate-400" />
              Account settings
            </Link>
            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              onClick={handleLogout}
              disabled={signingOut}
              aria-busy={signingOut || undefined}
              className={ITEM_CLASS}
            >
              {signingOut ? (
                <Spinner label={null} className="text-slate-400" />
              ) : (
                <LogOut aria-hidden="true" className="size-4 text-slate-400" />
              )}
              {signingOut ? 'Logging out…' : 'Log out'}
            </button>
          </div>
        </div>
      )}

      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={onButtonKeyDown}
        className={cn(
          'flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900',
          open ? 'bg-slate-100' : 'hover:bg-slate-100'
        )}
      >
        <span
          aria-hidden="true"
          className="flex size-8 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-semibold text-white"
        >
          {initials(user)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="sr-only">Account menu for </span>
          <span className="block truncate text-sm font-medium text-slate-900">
            {displayName}
          </span>
        </span>
        <ChevronsUpDown
          aria-hidden="true"
          className="size-4 shrink-0 text-slate-400"
        />
      </button>
    </div>
  );
}
