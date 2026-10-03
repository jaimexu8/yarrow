'use client';

import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';
import {
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

  return (
    <div className="flex min-h-0 flex-1 flex-col md:flex-row">
      <div
        id={ORIGINAL_PANE_ID}
        className={cn(
          'min-h-0 min-w-0 flex-1',
          mode === 'extracted' && 'hidden'
        )}
      >
        {original}
      </div>
      <PaneDivider />
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
