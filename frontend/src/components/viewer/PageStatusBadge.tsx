import { StatusBadge } from '@/components/ui/Badge';
import { Spinner } from '@/components/ui/Spinner';
import { cn } from '@/lib/cn';
import type { PageStatus } from '@/lib/progress';

const CANCELED_TONE = 'border-slate-300 bg-slate-100 text-slate-700';

export function PageStatusBadge({
  status,
  className,
}: {
  status: PageStatus | null;
  className?: string;
}) {
  if (!status) return null;
  return (
    <StatusBadge
      status={status}
      icon={
        status === 'processing' ? (
          <Spinner label={null} className="size-3" />
        ) : undefined
      }
      className={cn(status === 'canceled' && CANCELED_TONE, className)}
    />
  );
}
