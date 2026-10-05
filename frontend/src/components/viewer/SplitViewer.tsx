'use client';

import { useRef, useState, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import {
  DEFAULT_SPLIT,
  EXTRACTED_PANE_ID,
  ORIGINAL_PANE_ID,
  PaneDivider,
} from './PaneDivider';
import { useViewer } from './ViewerContext';

/**
 * The original and the extracted document side by side, with the divider
 * between them collapsing either one. They stack vertically on small screens.
 */
export function SplitViewer({
  original,
  extracted,
}: {
  original: ReactNode;
  extracted: ReactNode;
}) {
  const { mode } = useViewer();
  const container = useRef<HTMLDivElement>(null);
  const [split, setSplit] = useState(DEFAULT_SPLIT);

  return (
    <div ref={container} className="flex min-h-0 flex-1 flex-col md:flex-row">
      <div
        id={ORIGINAL_PANE_ID}
        className={cn(
          'min-h-0 min-w-0 flex-1',
          mode === 'extracted' && 'hidden'
        )}
        // Only the original pane is sized. The extracted pane takes the rest
        style={mode === 'split' ? { flex: `0 0 ${split * 100}%` } : undefined}
      >
        {original}
      </div>
      <PaneDivider
        split={split}
        onSplitChange={setSplit}
        container={container}
      />
      <div
        id={EXTRACTED_PANE_ID}
        className={cn(
          'min-h-0 min-w-0 flex-1',
          mode === 'original' && 'hidden'
        )}
      >
        {extracted}
      </div>
    </div>
  );
}
