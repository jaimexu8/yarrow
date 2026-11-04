'use client';

import Link from 'next/link';
import { cn } from '@/lib/cn';
import { formatBytes, type StorageLevel } from '@/lib/storage';
import { useStorageUsage } from '@/lib/useStorageUsage';

const FILL_COLORS: Record<StorageLevel, string> = {
  ok: 'bg-slate-900',
  warning: 'bg-amber-500',
  critical: 'bg-red-600',
  full: 'bg-red-600',
};

export function StorageQuotaIndicator({ className }: { className?: string }) {
  const { usage, failed } = useStorageUsage();

  if (!usage) {
    if (failed) {
      return (
        <p className={cn('text-sm text-slate-500', className)}>
          Storage usage is unavailable right now.
        </p>
      );
    }
    return (
      <div
        aria-hidden="true"
        className={cn('w-full animate-pulse space-y-2', className)}
      >
        <div className="h-5 w-32 rounded bg-slate-200" />
        <div className="h-2 rounded-full bg-slate-200" />
      </div>
    );
  }

  const text = `${formatBytes(usage.used_bytes)} of ${formatBytes(usage.quota_bytes)} used`;
  const filled = Math.min(
    usage.used_bytes + usage.reserved_bytes,
    usage.quota_bytes
  );
  const width =
    usage.quota_bytes > 0 ? (filled / usage.quota_bytes) * 100 : 100;

  return (
    <div className={cn('w-full space-y-2', className)}>
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium text-slate-900">{text}</span>
        {usage.trash_bytes > 0 && (
          <Link
            href="/trash"
            className="shrink-0 text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline focus-visible:rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
          >
            {formatBytes(usage.trash_bytes)} in trash
          </Link>
        )}
      </div>
      <div
        role="progressbar"
        aria-label="Storage used"
        aria-valuemin={0}
        aria-valuemax={usage.quota_bytes}
        aria-valuenow={filled}
        aria-valuetext={text}
        className="h-2 overflow-hidden rounded-full bg-slate-200"
      >
        <div
          className={cn(
            'h-full rounded-full transition-[width] duration-300',
            FILL_COLORS[usage.level]
          )}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}
