'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight } from 'lucide-react';
import { StatusBadge } from '@/components/ui/Badge';
import { cn } from '@/lib/cn';
import { FindInDocument } from './FindInDocument';
import { useViewer } from './ViewerContext';

const ICON_BUTTON =
  'rounded-md p-1.5 text-slate-600 hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:bg-transparent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900';

function PageIndicator() {
  const { currentPage, pageCount, scrollToPage } = useViewer();
  if (!pageCount) return null;
  return (
    <div className="flex items-center gap-1 text-sm text-slate-600">
      <button
        type="button"
        className={ICON_BUTTON}
        disabled={currentPage <= 1}
        onClick={() => scrollToPage(currentPage - 1)}
      >
        <ChevronLeft aria-hidden="true" className="size-4" />
        <span className="sr-only">Previous page</span>
      </button>
      <span
        aria-live="polite"
        className="min-w-[5.5rem] text-center tabular-nums"
      >
        Page {Math.min(currentPage, pageCount)} of {pageCount}
      </span>
      <button
        type="button"
        className={ICON_BUTTON}
        disabled={currentPage >= pageCount}
        onClick={() => scrollToPage(currentPage + 1)}
      >
        <ChevronRight aria-hidden="true" className="size-4" />
        <span className="sr-only">Next page</span>
      </button>
    </div>
  );
}

/**
 * The viewer's top bar
 */
export function ViewerHeader({ actions }: { actions?: ReactNode }) {
  const { document } = useViewer();
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
      <Link href="/dashboard" className={cn(ICON_BUTTON, '-ml-1.5')}>
        <ArrowLeft aria-hidden="true" className="size-4" />
        <span className="sr-only">Back to documents</span>
      </Link>
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <h1
          title={document.filename}
          className="truncate text-base font-semibold text-slate-900"
        >
          {document.filename}
        </h1>
        <StatusBadge status={document.status} className="shrink-0" />
      </div>
      <FindInDocument />
      <PageIndicator />
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}
