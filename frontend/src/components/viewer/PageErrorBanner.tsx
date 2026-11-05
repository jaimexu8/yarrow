'use client';

import { AlertTriangle } from 'lucide-react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { useReprocessAction } from '@/components/documents/documentActions';
import { cn } from '@/lib/cn';
import { useViewer } from './ViewerContext';

export function PageErrorBanner({
  pageNumber,
  className,
}: {
  pageNumber: number;
  className?: string;
}) {
  const { document, canEdit, pageStates, refresh } = useViewer();
  const reprocess = useReprocessAction(document, refresh, refresh);
  const state = pageStates.get(pageNumber);
  if (state?.status !== 'failed') return null;
  const offerReprocess = canEdit && reprocess.available;

  return (
    <Alert tone="error" className={cn('not-prose flex gap-2', className)}>
      <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">Page {pageNumber} could not be processed</p>
        {state.error_message && <p className="mt-0.5">{state.error_message}</p>}
        {reprocess.error && (
          <p className="mt-1 text-red-700">{reprocess.error}</p>
        )}
        {offerReprocess && (
          <Button
            variant="secondary"
            className="mt-2 w-auto bg-white px-3 py-1.5"
            loading={reprocess.reprocessing}
            onClick={reprocess.request}
          >
            {reprocess.reprocessing ? 'Starting…' : 'Reprocess'}
          </Button>
        )}
      </div>
      {reprocess.dialog}
    </Alert>
  );
}
