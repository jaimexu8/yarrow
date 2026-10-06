'use client';

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
} from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
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
 * The vertical divider between the side-by-side original and extracted panes,
 * with the arrows that collapse either one.
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

    return (event.clientX - rect.left) / rect.width;
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

    style.cursor = 'col-resize';
    style.userSelect = 'none';

    return () => {
      style.cursor = previous.cursor;
      style.userSelect = previous.userSelect;
    };
  }, [dragging]);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    let next: number | null = null;

    if (event.key === 'ArrowLeft') next = split - KEY_STEP;
    else if (event.key === 'ArrowRight') next = split + KEY_STEP;
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
        'group relative flex h-full w-8 shrink-0 flex-col items-center justify-center gap-2 bg-white',
        // The vertical rule between the panes
        'before:absolute before:inset-y-0 before:left-1/2 before:w-px before:bg-slate-200'
      )}
    >
      {mode === 'split' && (
        <div
          role="separator"
          tabIndex={0}
          aria-label="Resize panes"
          aria-orientation="vertical"
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
            'absolute inset-0 z-0 cursor-col-resize touch-none focus-visible:outline-none',
            // The rule darkens while hovering, dragging or focused.
            "after:absolute after:inset-y-0 after:left-1/2 after:w-0.5 after:-translate-x-1/2 after:bg-transparent after:transition-colors after:content-['']",
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
          <ChevronLeft aria-hidden="true" className="size-4" />
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
          <ChevronRight aria-hidden="true" className="size-4" />
        </button>
      )}
    </div>
  );
}
