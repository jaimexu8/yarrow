import { useViewer } from './ViewerContext';
import type { PageOverlayInfo } from './PageView';
import { cn } from '@/lib/cn';

export function BoundingBoxCanvas({
  page,
  displayWidth,
  displayHeight,
}: PageOverlayInfo) {
  const { activeRegionId, setActiveRegionId } = useViewer();

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
        const isActive = activeRegionId === region.id;

        return (
          <div
            key={region.id}
            className={cn(
              'absolute border-2 pointer-events-auto transition-colors cursor-pointer',
              isActive
                ? 'border-blue-500 bg-blue-500/20 z-20'
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
          />
        );
      })}
    </div>
  );
}
