'use client';

import { useEffect, useRef } from 'react';
import { AlertTriangle, Info, Loader2, RotateCw } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useReprocessAction } from '@/components/documents/documentActions';
import { isInterrupted, isWorkingOn } from '@/lib/documents';
import { cn } from '@/lib/cn';
import { useViewer } from './ViewerContext';

/** "7, 19" or "1, 2, …, 10 and 5 more", like the worker's own summaries. */
function listPages(pages: number[], total = pages.length): string {
  const shown = pages.slice(0, 10).join(', ');
  return total > 10 ? `${shown} and ${total - 10} more` : shown;
}

const TONES = {
  warning: 'border-amber-200 bg-amber-50 text-amber-900',
  neutral: 'border-slate-200 bg-slate-50 text-slate-700',
  progress: 'border-sky-200 bg-sky-50 text-sky-900',
};

/**
 * The document-wide bar under the viewer header that shows unfinished
 * work (failed pages, or a run that was interrupted) with the Reprocess
 * action that resumes it, and the progress of a reprocess once it runs.
 * Renders nothing when all is well.
 */
export function ReprocessBar({ onReprocessed }: { onReprocessed: () => void }) {
  const { document, tree, pageCount } = useViewer();
  const reprocess = useReprocessAction(document, onReprocessed, onReprocessed);
  const message = useRef<HTMLParagraphElement>(null);
  const clicked = useRef(false);

  const finished = (tree?.pages ?? [])
    .filter((page) => page.status === 'completed')
    .map((page) => page.page_number);
  const working = isWorkingOn(document);

  // The button disappears once the run starts
  useEffect(() => {
    if (clicked.current && working) {
      clicked.current = false;
      message.current?.focus({ preventScroll: true });
    }
  }, [working]);

  const info = document.reprocess;
  let tone: keyof typeof TONES;
  let text: string;
  let offerAction = false;

  if (info?.scope === 'incomplete' && info.interrupted) {
    // The run was interrupted before it finished

    tone = 'neutral';
    offerAction = true;
    text = 'Processing stopped before it finished.';
    if (pageCount) {
      text += ` ${finished.length} of ${pageCount} pages are ready.`;
    }

  } else if (info?.scope === 'incomplete') {
    // The run failed before it finished

    tone = 'warning';
    offerAction = true;
    text = info.pages === null ? 'Processing failed before any page was read.'
      : `${info.pages} ${info.pages === 1 ? 'page' : 'pages'} couldn't be read: ${listPages(info.page_numbers, info.pages)}.`;

  } else if (working && finished.length > 0) {
    // A reprocess is running: finished pages stay on screen meanwhile.

    tone = 'progress';
    const remaining = pageCount ? Array.from({ length: pageCount }, (_, i) => i + 1).filter(
      (number) => !finished.includes(number)
    ) : [];
    text = remaining.length
      ? `Processing ${remaining.length === 1 ? 'page' : 'pages'} ${listPages(remaining)}… The rest of the document is shown below.`
      : 'Reprocessing the document… The current results are shown until it finishes.';
  } else {
    return null;
  }

  const Icon =
    tone === 'warning' ? AlertTriangle : tone === 'progress' ? Loader2 : Info;

  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2.5 text-sm sm:px-6',
        TONES[tone]
      )}
    >
      <p
        ref={message}
        tabIndex={-1}
        role="status"
        className="flex min-w-0 flex-1 items-start gap-2 focus:outline-none"
      >
        <Icon
          aria-hidden="true"
          className={cn(
            'mt-0.5 size-4 shrink-0',
            tone === 'progress' && 'animate-spin motion-reduce:animate-none'
          )}
        />
        <span>
          {text}
          {reprocess.error && (
            <span className="block text-red-700">{reprocess.error}</span>
          )}
        </span>
      </p>
      {offerAction && (
        <Button
          variant="secondary"
          className="w-auto bg-white px-3 py-1.5"
          loading={reprocess.reprocessing}
          onClick={() => {
            clicked.current = true;
            reprocess.request();
          }}
        >
          {reprocess.reprocessing ? 'Starting…' : 'Reprocess'}
        </Button>
      )}
    </div>
  );
}

/**
 * The header's Reprocess button for a finished document, which
 * processes every page again after a confirmation
 */
export function ReprocessButton({
  onReprocessed,
}: {
  onReprocessed: () => void;
}) {
  const { document } = useViewer();
  const reprocess = useReprocessAction(document, onReprocessed, onReprocessed);
  if (document.reprocess?.scope !== 'all') return null;
  return (
    <>
      <Button
        variant="secondary"
        className="w-auto px-3 py-1.5"
        onClick={reprocess.request}
      >
        <RotateCw aria-hidden="true" className="size-4" />
        Reprocess
      </Button>
      {reprocess.dialog}
    </>
  );
}
