import { STATUS_LABELS, type DocumentStatus } from '@/lib/documents';
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
    label: STATUS_LABELS.queued,
    className: 'border-slate-300 bg-slate-100 text-slate-700',
  },
  processing: {
    label: STATUS_LABELS.processing,
    className: 'border-sky-200 bg-sky-50 text-sky-800',
  },
  completed: {
    label: STATUS_LABELS.completed,
    className: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  },
  failed: {
    label: STATUS_LABELS.failed,
    className: 'border-red-200 bg-red-50 text-red-800',
  },
  canceled: {
    label: STATUS_LABELS.canceled,
    className: 'border-amber-200 bg-amber-50 text-amber-800',
  },
};

const INTERRUPTED_STYLE = {
  label: 'Interrupted',
  className: 'border-orange-200 bg-orange-50 text-orange-800',
};

export function StatusBadge({
  status,
  interrupted = false,
  className,
}: {
  status: DocumentStatus | null;
  interrupted?: boolean;
  className?: string;
}) {
  const style = interrupted
    ? INTERRUPTED_STYLE
    : status
      ? STATUS_STYLES[status]
      : undefined;
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
