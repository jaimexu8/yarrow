import type { DocumentStatus } from '@/lib/documents';
import { cn } from '@/lib/cn';

/**
 * A document's processing status. Each state has its own label and colour,
 * and the label is always shown, so states are never told apart by colour
 * alone (NFR-13: queued, processing, completed and failed must be clearly
 * distinguishable).
 */
const STATUS_STYLES: Record<
  DocumentStatus,
  { label: string; className: string }
> = {
  queued: {
    label: 'Queued',
    className: 'border-slate-300 bg-slate-100 text-slate-700',
  },
  processing: {
    label: 'Processing',
    className: 'border-sky-200 bg-sky-50 text-sky-800',
  },
  completed: {
    label: 'Completed',
    className: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  },
  failed: {
    label: 'Failed',
    className: 'border-red-200 bg-red-50 text-red-800',
  },
  canceled: {
    label: 'Canceled',
    className: 'border-amber-200 bg-amber-50 text-amber-800',
  },
};

export function StatusBadge({
  status,
  className,
}: {
  status: DocumentStatus | null;
  className?: string;
}) {
  const style = status ? STATUS_STYLES[status] : undefined;
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium',
        style?.className ?? 'border-slate-300 bg-white text-slate-600',
        className
      )}
    >
      {style?.label ?? 'Unknown'}
    </span>
  );
}
