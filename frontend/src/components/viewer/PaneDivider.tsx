'use client';

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
} from 'react';
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

// The original pane's share of the space when the viewer opens
export const DEFAULT_SPLIT = 0.5;

const MIN_SPLIT = 0.2;
const MAX_SPLIT = 0.8;

// How far one arrow key press moves the divider.
const KEY_STEP = 0.05;

function clampSplit(value: number): number {
  return Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, value));
}

// Whether the panes sit side by side (md and up) or stack
function useSideBySide(): boolean {
  const [sideBySide, setSideBySide] = useState(true);
  useEffect(() => {
    const query = window.matchMedia('(min-width: 768px)');
    const update = () => setSideBySide(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return sideBySide;
}

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
export function PaneDivider({
  split,
  onSplitChange,
  container,
}: {
  split: number;
  onSplitChange: (split: number) => void;
  container: RefObject<HTMLElement>;
}) {
  const { mode, setMode } = useViewer();
  const sideBySide = useSideBySide();
  const [dragging, setDragging] = useState(false);
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

  function splitAt(event: PointerEvent<HTMLElement>): number | null {
    const rect = container.current?.getBoundingClientRect();
    if (!rect) return null;

    return sideBySide
      ? (event.clientX - rect.left) / rect.width
      : (event.clientY - rect.top) / rect.height;
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();

    // Keep receiving moves even when the pointer leaves the thin handle.
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!dragging) return;

    const next = splitAt(event);
    if (next !== null) onSplitChange(clampSplit(next));
  }

  function endDrag(event: PointerEvent<HTMLDivElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
  }

  // While dragging, keep the resize cursor everywhere and stop the drag from
  // selecting text in the panes.
  useEffect(() => {
    if (!dragging) return;

    const { style } = window.document.body;
    const previous = { cursor: style.cursor, userSelect: style.userSelect };

    style.cursor = sideBySide ? 'col-resize' : 'row-resize';
    style.userSelect = 'none';

    return () => {
      style.cursor = previous.cursor;
      style.userSelect = previous.userSelect;
    };
  }, [dragging, sideBySide]);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const smaller = sideBySide ? 'ArrowLeft' : 'ArrowUp';
    const larger = sideBySide ? 'ArrowRight' : 'ArrowDown';
    let next: number | null = null;

    if (event.key === smaller) next = split - KEY_STEP;
    else if (event.key === larger) next = split + KEY_STEP;
    else if (event.key === 'Home') next = MIN_SPLIT;
    else if (event.key === 'End') next = MAX_SPLIT;

    if (next === null) return;

    event.preventDefault();
    onSplitChange(clampSplit(next));
  }

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
        'group relative flex shrink-0 items-center justify-center gap-2 bg-white',
        // The rule: horizontal across stacked panes, vertical between
        // side-by-side ones.
        'h-8 w-full flex-row before:absolute before:inset-x-0 before:top-1/2 before:h-px before:bg-slate-200',
        'md:h-full md:w-8 md:flex-col md:before:inset-x-auto md:before:inset-y-0 md:before:left-1/2 md:before:top-0 md:before:h-auto md:before:w-px'
      )}
    >
      {mode === 'split' && (
        <div
          role="separator"
          tabIndex={0}
          aria-label="Resize panes"
          aria-orientation={sideBySide ? 'vertical' : 'horizontal'}
          aria-controls={ORIGINAL_PANE_ID}
          aria-valuemin={MIN_SPLIT * 100}
          aria-valuemax={MAX_SPLIT * 100}
          aria-valuenow={Math.round(split * 100)}
          aria-valuetext={`Original ${Math.round(split * 100)}%, extracted text ${Math.round((1 - split) * 100)}%`}
          title="Drag to resize; double-click to reset"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onDoubleClick={() => onSplitChange(DEFAULT_SPLIT)}
          onKeyDown={onKeyDown}
          className={cn(
            'absolute inset-0 z-0 touch-none focus-visible:outline-none',
            sideBySide ? 'cursor-col-resize' : 'cursor-row-resize',
            // The rule darkens while hovering, dragging or focused.
            "after:absolute after:bg-transparent after:transition-colors after:content-['']",
            'after:inset-x-0 after:top-1/2 after:h-0.5 after:-translate-y-1/2',
            'md:after:inset-x-auto md:after:inset-y-0 md:after:left-1/2 md:after:top-0 md:after:h-auto md:after:w-0.5 md:after:-translate-x-1/2 md:after:translate-y-0',
            'hover:after:bg-slate-400 focus-visible:after:bg-slate-900',
            dragging && 'after:bg-slate-500'
          )}
        />
      )}
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
