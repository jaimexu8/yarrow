'use client';

import { useEffect, useRef } from 'react';
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
} from 'lucide-react';
import { cn } from '@/lib/cn';
import { useViewer, type ViewMode } from './ViewerContext';

export const ORIGINAL_PANE_ID = 'viewer-original-pane';
export const EXTRACTED_PANE_ID = 'viewer-extracted-pane';

type Direction = 'toward-original' | 'toward-extracted';

// Each arrow pushes the divider its way, always passing through split
const NEXT_MODE: Record<Direction, Partial<Record<ViewMode, ViewMode>>> = {
  // The original side shrinks
  'toward-original': { split: 'extracted', original: 'split' },

  // The extracted side shrinks
  'toward-extracted': { split: 'original', extracted: 'split' },
};

const BUTTON =
  'relative z-10 flex size-6 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 shadow-sm transition-colors hover:border-slate-300 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900';

/**
 * The divider between the original and the extracted pane, with the arrows
 * that collapse either one. Vertical on desktop and horizontal on mobile.
 */
export function PaneDivider() {
  const { mode, setMode } = useViewer();
  const towardOriginal = useRef<HTMLButtonElement>(null);
  const towardExtracted = useRef<HTMLButtonElement>(null);
  const clicked = useRef<Direction | null>(null);

  // Keep keyboard focus on the arrow that remains after some buttons are hidden
  useEffect(() => {
    const last = clicked.current;
    clicked.current = null;
    if (last === 'toward-original' && mode === 'extracted') {
      towardExtracted.current?.focus({ preventScroll: true });
    } else if (last === 'toward-extracted' && mode === 'original') {
      towardOriginal.current?.focus({ preventScroll: true });
    }
  }, [mode]);

  function push(direction: Direction) {
    const next = NEXT_MODE[direction][mode];
    if (!next) return;
    clicked.current = direction;
    setMode(next);
  }

  // Determine the labels, controls, and expanded state for the arrows based on the current mode.
  const originalArrow =
    mode === 'split'
      ? { label: 'Hide original', controls: ORIGINAL_PANE_ID, expanded: true }
      : {
          label: 'Show extracted text',
          controls: EXTRACTED_PANE_ID,
          expanded: false,
        };
  const extractedArrow =
    mode === 'split'
      ? {
          label: 'Hide extracted text',
          controls: EXTRACTED_PANE_ID,
          expanded: true,
        }
      : { label: 'Show original', controls: ORIGINAL_PANE_ID, expanded: false };

  return (
    <div
      className={cn(
        'relative flex shrink-0 items-center justify-center gap-2 bg-white',
        // The rule: horizontal across stacked panes, vertical between
        // side-by-side ones.
        'h-8 w-full flex-row before:absolute before:inset-x-0 before:top-1/2 before:h-px before:bg-slate-200',
        'md:h-full md:w-8 md:flex-col md:before:inset-x-auto md:before:inset-y-0 md:before:left-1/2 md:before:top-0 md:before:h-auto md:before:w-px'
      )}
    >
      {mode !== 'extracted' && (
        <button
          ref={towardOriginal}
          type="button"
          onClick={() => push('toward-original')}
          aria-label={originalArrow.label}
          aria-controls={originalArrow.controls}
          aria-expanded={originalArrow.expanded}
          title={originalArrow.label}
          className={BUTTON}
        >
          <ChevronUp aria-hidden="true" className="size-4 md:hidden" />
          <ChevronLeft aria-hidden="true" className="hidden size-4 md:block" />
        </button>
      )}
      {mode !== 'original' && (
        <button
          ref={towardExtracted}
          type="button"
          onClick={() => push('toward-extracted')}
          aria-label={extractedArrow.label}
          aria-controls={extractedArrow.controls}
          aria-expanded={extractedArrow.expanded}
          title={extractedArrow.label}
          className={BUTTON}
        >
          <ChevronDown aria-hidden="true" className="size-4 md:hidden" />
          <ChevronRight aria-hidden="true" className="hidden size-4 md:block" />
        </button>
      )}
    </div>
  );
}
