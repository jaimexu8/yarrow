'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Menu, X } from 'lucide-react';
import { AccountMenu } from './AccountMenu';
import { SidebarNav } from './SidebarNav';
import { cn } from '@/lib/cn';

function Brand({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <Link
      href="/dashboard"
      onClick={onNavigate}
      className="inline-flex flex-col rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-slate-900"
    >
      <span className="inline-flex items-center gap-2 text-lg font-semibold tracking-tight text-slate-900">
        <span
          aria-hidden="true"
          className="size-2.5 rounded-full bg-amber-400"
        />
        Yarrow
      </span>
      <span className="pl-[18px] text-xs text-slate-500">
        Document Intelligence
      </span>
    </Link>
  );
}

/** Brand, section links and, pinned to the bottom, the account menu. */
function SidebarContents({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col px-3 py-5">
      <div className="px-2">
        <Brand onNavigate={onNavigate} />
      </div>
      <div className="mt-8 flex-1 overflow-y-auto">
        <SidebarNav onNavigate={onNavigate} />
      </div>
      <div className="mt-4 border-t border-slate-200 pt-3">
        <AccountMenu />
      </div>
    </div>
  );
}

// Routes that should take up the full width and height of the viewport, 
const FULL_BLEED_PREFIXES = ['/documents/'];

/**
 * Layout for every signed-in page: a fixed sidebar on desktop and a top bar 
 * on mobile whose menu button opens the same sidebar
 */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const fullBleed = FULL_BLEED_PREFIXES.some((prefix) =>
    pathname.startsWith(prefix)
  );
  const [drawerOpen, setDrawerOpen] = useState(false);
  const openButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  function closeDrawer() {
    setDrawerOpen(false);
    openButtonRef.current?.focus();
  }

  // Following a link closes the drawer.
  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  // While the drawer is open: focus it, close on Escape, and stop the page
  // behind it from scrolling.
  useEffect(() => {
    if (!drawerOpen) return;
    closeButtonRef.current?.focus();
    function onKeyDown(event: globalThis.KeyboardEvent) {
      // An open account menu handles its own Escape first.
      if (event.key === 'Escape' && !event.defaultPrevented) {
        setDrawerOpen(false);
        openButtonRef.current?.focus();
      }
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [drawerOpen]);

  return (
    <div
      className={cn(
        'bg-white',
        // Full-bleed pages get exactly one screen of height, split between
        // the mobile top bar and the page, so the page can scroll inside.
        fullBleed ? 'flex h-dvh flex-col md:flex-row' : 'min-h-screen md:flex'
      )}
    >
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-slate-900 focus:shadow"
      >
        Skip to content
      </a>

      {/* Mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur md:hidden">
        <Brand />
        <button
          ref={openButtonRef}
          type="button"
          onClick={() => setDrawerOpen(true)}
          aria-expanded={drawerOpen}
          aria-controls="mobile-sidebar"
          className="rounded-lg p-2 text-slate-700 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          <Menu aria-hidden="true" className="size-5" />
          <span className="sr-only">Open navigation</span>
        </button>
      </header>

      {/* Mobile drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div
            aria-hidden="true"
            onClick={closeDrawer}
            className="absolute inset-0 bg-slate-900/30"
          />
          <aside
            id="mobile-sidebar"
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="absolute inset-y-0 left-0 w-72 max-w-[85vw] border-r border-slate-200 bg-slate-50 shadow-xl"
          >
            <button
              ref={closeButtonRef}
              type="button"
              onClick={closeDrawer}
              className="absolute right-3 top-4 rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
            >
              <X aria-hidden="true" className="size-5" />
              <span className="sr-only">Close navigation</span>
            </button>
            <SidebarContents onNavigate={() => setDrawerOpen(false)} />
          </aside>
        </div>
      )}

      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 border-r border-slate-200 bg-slate-50 md:block">
        <SidebarContents />
      </aside>

      <main
        id="main-content"
        tabIndex={-1}
        className={cn(
          'min-w-0 flex-1 focus:outline-none',
          fullBleed && 'min-h-0 overflow-hidden'
        )}
      >
        {fullBleed ? (
          children
        ) : (
          <div className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-8">
            {children}
          </div>
        )}
      </main>
    </div>
  );
}
