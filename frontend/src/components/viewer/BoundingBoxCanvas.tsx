import { useViewer } from './ViewerContext';
import type { PageOverlayInfo } from './PageView';
import { isRegionFilterMatch } from './regionRenderers';
import { cn } from '@/lib/cn';
import { isLowConfidence } from '@/lib/viewer';

export function BoundingBoxCanvas({
  page,
  displayWidth,
  displayHeight,
}: PageOverlayInfo) {
  const { activeRegionId, setActiveRegionId, regionFilter, showLowConfidence } =
    useViewer();

  if (!page || !page.width || !page.height) return null;

  const scaleX = displayWidth / page.width;
  const scaleY = displayHeight / page.height;

  return (
    <div className="absolute inset-0 z-10 overflow-hidden pointer-events-none">
      {page.regions.map((region) => {
        if (!region.bbox) return null;
        const [x0, y0, x1, y1] = [
          region.bbox.x0,
          region.bbox.y0,
          region.bbox.x1,
          region.bbox.y1,
        ];
        const { confidence } = region;
        const isActive = activeRegionId === region.id;
        const isFilterMatch = isRegionFilterMatch(region, regionFilter);
        const flagged = showLowConfidence && isLowConfidence(region);

        return (
          <div
            key={region.id}
            data-region-id={region.id}
            className={cn(
              'absolute border-2 pointer-events-auto transition-colors cursor-pointer',
              isActive
                ? 'border-blue-500 bg-blue-500/20 z-20 ring-2 ring-blue-400'
                : isFilterMatch
                  ? 'border-indigo-500 bg-indigo-500/20 z-15 ring-1 ring-indigo-400'
                  : flagged
                    ? 'border-yellow-500 bg-yellow-500/15 z-10 hover:bg-yellow-500/25'
                    : 'border-transparent hover:border-blue-300 hover:bg-blue-300/10 z-10'
            )}
            style={{
              left: Math.round(x0 * scaleX),
              top: Math.round(y0 * scaleY),
              width: Math.max(Math.round((x1 - x0) * scaleX), 2),
              height: Math.max(Math.round((y1 - y0) * scaleY), 2),
            }}
            onClick={() => setActiveRegionId(isActive ? null : region.id)}
            onMouseEnter={() => setActiveRegionId(region.id)}
            onMouseLeave={() => {
              if (activeRegionId === region.id) setActiveRegionId(null);
            }}
          >
            {confidence != null && (
              <span
                className={cn(
                  'absolute left-0 top-0 -translate-y-full rounded-sm px-1 text-[10px] leading-4 whitespace-nowrap',
                  isActive
                    ? 'bg-blue-600 text-white'
                    : flagged
                      ? 'bg-yellow-600 text-white'
                      : 'bg-transparent text-transparent'
                )}
              >
                {(confidence <= 1 ? confidence * 100 : confidence).toFixed(0)}%
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
