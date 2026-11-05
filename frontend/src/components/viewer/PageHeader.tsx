import { cn } from '@/lib/cn';
import { PageStatusBadge } from './PageStatusBadge';
import { useViewer } from './ViewerContext';

export function PageHeader({
  pageNumber,
  headingId,
  as: Heading = 'h3',
  className,
}: {
  pageNumber: number;
  headingId?: string;
  as?: 'h2' | 'h3';
  className?: string;
}) {
  const { pageStates } = useViewer();
  return (
    <div
      className={cn(
        'not-prose flex items-center justify-between gap-3',
        className
      )}
    >
      <Heading
        id={headingId}
        className="m-0 text-xs font-medium tracking-wide text-slate-500 uppercase"
      >
        Page {pageNumber}
      </Heading>
      <PageStatusBadge status={pageStates.get(pageNumber)?.status ?? null} />
    </div>
  );
}
