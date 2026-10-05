'use client';

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { flushSync } from 'react-dom';
import { cn } from '@/lib/cn';
import {
  DEFAULT_SPLIT,
  EXTRACTED_PANE_ID,
  ORIGINAL_PANE_ID,
  PaneDivider,
} from './PaneDivider';
import { useViewer, type Pane } from './ViewerContext';

const SIDE_BY_SIDE_QUERY = '(min-width: 768px)';

const TABS: { pane: Pane; label: string; panel: string; id: string }[] = [
  {
    pane: 'original',
    label: 'Original',
    panel: ORIGINAL_PANE_ID,
    id: 'viewer-original-tab',
  },
  {
    pane: 'extracted',
    label: 'Extracted text',
    panel: EXTRACTED_PANE_ID,
    id: 'viewer-extracted-tab',
  },
];

// Whether there is room for the panes side by side (md and up)
function useSideBySide(): boolean {
  const [sideBySide, setSideBySide] = useState(
    () => window.matchMedia(SIDE_BY_SIDE_QUERY).matches
  );
  useEffect(() => {
    const query = window.matchMedia(SIDE_BY_SIDE_QUERY);
    const update = () => setSideBySide(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return sideBySide;
}

/**
 * The original and the extracted document side by side, with the divider
 * between them collapsing either one. Small screens get one pane at a time,
 * switched with tabs.
 */
export function SplitViewer({
  original,
  extracted,
}: {
  original: ReactNode;
  extracted: ReactNode;
}) {
  const { mode, setMode, currentPage, scrollToPage } = useViewer();
  const sideBySide = useSideBySide();
  const container = useRef<HTMLDivElement>(null);
  const tabs = useRef<Record<Pane, HTMLButtonElement | null>>({
    original: null,
    extracted: null,
  });
  const [split, setSplit] = useState(DEFAULT_SPLIT);

  const tab: Pane = mode === 'extracted' ? 'extracted' : 'original';

  const shown: Record<Pane, boolean> = sideBySide
    ? { original: mode !== 'extracted', extracted: mode !== 'original' }
    : { original: tab === 'original', extracted: tab === 'extracted' };

  function selectTab(pane: Pane) {
    // Return early if the selected tab is already active and we're not in split mode
    if (pane === tab && mode !== 'split') return;

    // Show the pane first: scrollToPage skips hidden panes
    flushSync(() => setMode(pane));
    scrollToPage(currentPage, pane);
  }

  // Handle keyboard navigation for the tabs (ArrowLeft, ArrowRight, Home, End)
  function onTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      return;
    }
    event.preventDefault();

    let next: Pane;
    if (event.key === 'Home') next = 'original';
    else if (event.key === 'End') next = 'extracted';
    else if (tab === 'original') next = 'extracted';
    else next = 'original';

    selectTab(next);
    tabs.current[next]?.focus();
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {!sideBySide && (
        <div
          role="tablist"
          aria-label="Document view"
          className="flex shrink-0 border-b border-slate-200 bg-white"
        >
          {TABS.map(({ pane, label, panel, id }) => (
            <button
              key={pane}
              ref={(element) => {
                tabs.current[pane] = element;
              }}
              id={id}
              type="button"
              role="tab"
              aria-selected={tab === pane}
              aria-controls={panel}
              tabIndex={tab === pane ? 0 : -1}
              onClick={() => selectTab(pane)}
              onKeyDown={onTabKeyDown}
              className={cn(
                '-mb-px flex-1 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-slate-900',
                tab === pane
                  ? 'border-slate-900 text-slate-900'
                  : 'border-transparent text-slate-500 hover:text-slate-900'
              )}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      <div ref={container} className="flex min-h-0 flex-1">
        <div
          id={ORIGINAL_PANE_ID}
          role={sideBySide ? undefined : 'tabpanel'}
          aria-labelledby={sideBySide ? undefined : TABS[0].id}
          className={cn('min-h-0 min-w-0 flex-1', !shown.original && 'hidden')}
          // Only the original pane is sized. The extracted pane takes the rest
          style={
            sideBySide && mode === 'split'
              ? { flex: `0 0 ${split * 100}%` }
              : undefined
          }
        >
          {original}
        </div>
        {sideBySide && (
          <PaneDivider
            split={split}
            onSplitChange={setSplit}
            container={container}
          />
        )}
        <div
          id={EXTRACTED_PANE_ID}
          role={sideBySide ? undefined : 'tabpanel'}
          aria-labelledby={sideBySide ? undefined : TABS[1].id}
          className={cn('min-h-0 min-w-0 flex-1', !shown.extracted && 'hidden')}
        >
          {extracted}
        </div>
      </div>
    </div>
  );
}
