'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { RotateCw } from 'lucide-react';
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import { Spinner } from '@/components/ui/Spinner';
import { cn } from '@/lib/cn';
import { getPageImage, type PageNode } from '@/lib/viewer';
import { useViewer } from './ViewerContext';

/** What an overlay needs to line itself up with a rendered page. */
export type PageOverlayInfo = {
  pageNumber: number;
  displayWidth: number;
  displayHeight: number;

  // The parsed page. Null until the parsed content has loaded, or
  // for a page that was not processed.
  page: PageNode | null;
};

/**
 * Drawn above a page, sized to it. Used to render overlays such as bounding boxes.
 */
export type RenderPageOverlay = (info: PageOverlayInfo) => ReactNode;

// Start rendering a little before a page scrolls into view.
const RENDER_MARGIN = '800px 0px';

/** The frame every original page sits in, PDF or image alike. */
export function PageFrame({
  pageNumber,
  aspectRatio,
  children,
  renderOverlay,
}: {
  pageNumber: number;
  aspectRatio: number;
  children: ReactNode;
  renderOverlay?: RenderPageOverlay;
}) {
  const { tree, registerPage, pageRotations, rotatePage } = useViewer();
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const rotation = pageRotations[pageNumber] ?? 0;

  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({
        width: entry.contentRect.width,
        height: entry.contentRect.height,
      });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const page =
    tree?.pages.find((candidate) => candidate.page_number === pageNumber) ??
    null;

  // Turning a page a quarter turn swaps its width and height against the
  // frame. The content is scaled down to fit the frame in both directions so
  // nothing is clipped, leaving symmetric margins. 0 and 180 keep the full
  // size; 90 and 270 scale to the smaller axis.
  const rotatedScale =
    size.width > 0 && (rotation === 90 || rotation === 270)
      ? Math.min(
          size.width / (size.height || 1),
          (size.height || 1) / (size.width || 1)
        )
      : 1;
  const contentTransform = rotation
    ? `rotate(${rotation}deg) scale(${rotatedScale})`
    : undefined;

  return (
    <section
      ref={(element) => registerPage('original', pageNumber, element)}
      data-viewer-page={pageNumber}
      data-pane="original"
      aria-label={`Page ${pageNumber}`}
    >
      <div
        ref={frame}
        style={{ aspectRatio }}
        className="relative w-full overflow-hidden rounded-sm bg-white shadow-md ring-1 ring-slate-900/5"
      >
        {/* The content and its overlay turn as one unit so bounding boxes
            stay aligned with the displayed page. */}
        <div
          style={
            rotation
              ? {
                  transform: contentTransform,
                  transformOrigin: 'center center',
                }
              : undefined
          }
          className="absolute inset-0"
        >
          {children}
          {renderOverlay && size.width > 0 && (
            <div className="absolute inset-0">
              {renderOverlay({
                pageNumber,
                displayWidth: size.width,
                displayHeight: size.height,
                page,
              })}
            </div>
          )}
        </div>
        <button
          type="button"
          aria-label={`Rotate page ${pageNumber} clockwise`}
          title="Rotate clockwise"
          onClick={() => rotatePage(pageNumber)}
          className="absolute bottom-2 right-2 z-20 flex h-8 items-center gap-1 rounded-md border border-slate-300 bg-white/95 px-2 text-slate-600 shadow-sm transition-colors hover:bg-slate-50 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          <RotateCw className="size-4" />
          {rotation > 0 && (
            <span className="text-xs font-medium tabular-nums">
              {rotation}°
            </span>
          )}
        </button>
      </div>
    </section>
  );
}

/**
 * One PDF page, drawn to a canvas only once it is near the viewport and
 * redrawn at the new size when the pane is resized, so it stays sharp.
 */
export function PdfPageView({
  pdf,
  pageNumber,
  aspectRatio,
  renderOverlay,
}: {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  aspectRatio: number;
  renderOverlay?: RenderPageOverlay;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [nearViewport, setNearViewport] = useState(false);
  const [width, setWidth] = useState(0);
  const [drawn, setDrawn] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const element = holder.current;
    if (!element) return;

    const root = element.closest<HTMLElement>('[data-viewer-scroll]');
    const intersection = new IntersectionObserver(
      ([entry]) => setNearViewport(entry.isIntersecting),
      { root, rootMargin: RENDER_MARGIN }
    );
    const resize = new ResizeObserver(([entry]) =>
      setWidth(Math.round(entry.contentRect.width))
    );
    intersection.observe(element);
    resize.observe(element);
    return () => {
      intersection.disconnect();
      resize.disconnect();
    };
  }, []);

  useEffect(() => {
    if (!nearViewport || width === 0 || !canvas.current) return;
    let task: RenderTask | null = null;
    let cancelled = false;

    (async () => {
      try {
        const page = await pdf.getPage(pageNumber);
        if (cancelled || !canvas.current) return;
        const base = page.getViewport({ scale: 1 });
        const ratio = window.devicePixelRatio || 1;
        const viewport = page.getViewport({
          scale: (width / base.width) * ratio,
        });
        const target = canvas.current;
        target.width = Math.floor(viewport.width);
        target.height = Math.floor(viewport.height);
        const context = target.getContext('2d');
        if (!context) return;
        // pdf.js v6 requires the canvas itself; the context is kept because
        // the canvas is the one this effect just sized up.
        task = page.render({
          canvas: target,
          canvasContext: context,
          viewport,
        });
        await task.promise;
        if (!cancelled) setDrawn(true);
      } catch (error) {
        // A newer render (e.g. after a resize) cancelled this one.
        if (
          (error as { name?: string })?.name === 'RenderingCancelledException'
        )
          return;
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, pageNumber, nearViewport, width]);

  return (
    <PageFrame
      pageNumber={pageNumber}
      aspectRatio={aspectRatio}
      renderOverlay={renderOverlay}
    >
      <div ref={holder} className="absolute inset-0">
        <canvas
          ref={canvas}
          aria-hidden="true"
          className={cn('size-full', !drawn && 'invisible')}
        />
        {!drawn && !failed && (
          <div className="absolute inset-0 flex items-center justify-center text-slate-400">
            {nearViewport && <Spinner label={`Rendering page ${pageNumber}`} />}
          </div>
        )}
        {failed && (
          <p className="absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-slate-500">
            This page couldn&apos;t be displayed.
          </p>
        )}
      </div>
    </PageFrame>
  );
}

/**
 * The button that switches a rotated page between its corrected upright image
 * and the raw original.
 */
export function OriginalToggle({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="absolute left-2 top-2 z-20 rounded-md border border-slate-300 bg-white/95 px-2 py-1 text-xs font-medium text-slate-900 shadow-sm transition-colors hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
    >
      {label}
    </button>
  );
}

/**
 * A page that was rotated before OCR, shown as its corrected upright image
 * instead of the raw original. The corrected image's own aspect ratio sizes
 * the frame so the bounding boxes (which scale against the page's corrected
 * width and height) stay aligned with the displayed image. The reader can
 * switch back to the original to compare.
 */
export function CorrectedPageView({
  documentId,
  pageNumber,
  fallbackAspectRatio,
  renderOverlay,
}: {
  documentId: string;
  pageNumber: number;
  // The original page's aspect, used until the corrected image's own aspect
  // is known, so the frame does not jump on load.
  fallbackAspectRatio: number;
  renderOverlay?: RenderPageOverlay;
}) {
  const { document } = useViewer();
  const [image, setImage] = useState<
    | { state: 'loading' }
    | { state: 'ready'; url: string; aspectRatio: number }
    | { state: 'failed' }
  >({ state: 'loading' });

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;

    getPageImage(documentId, pageNumber)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        const objectUrl = url;
        // Probe the natural size with the same URL the <img> will use, so
        // the frame can adopt the corrected image's own aspect.
        const img = new Image();
        img.onload = () => {
          if (cancelled) return;
          setImage({
            state: 'ready',
            url: objectUrl,
            aspectRatio:
              img.naturalWidth / img.naturalHeight || fallbackAspectRatio,
          });
        };
        img.onerror = () => {
          if (!cancelled) setImage({ state: 'failed' });
        };
        img.src = objectUrl;
      })
      .catch(() => {
        if (!cancelled) setImage({ state: 'failed' });
      });

    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [documentId, pageNumber, fallbackAspectRatio]);

  const aspectRatio =
    image.state === 'ready' ? image.aspectRatio : fallbackAspectRatio;

  return (
    <PageFrame
      pageNumber={pageNumber}
      aspectRatio={aspectRatio}
      renderOverlay={renderOverlay}
    >
      {image.state === 'failed' ? (
        <p className="absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-slate-500">
          The corrected page couldn&apos;t be displayed.
        </p>
      ) : (
        <img
          src={image.state === 'ready' ? image.url : undefined}
          alt={`Corrected page ${pageNumber} of ${document.filename}`}
          className="absolute inset-0 size-full object-contain"
        />
      )}
    </PageFrame>
  );
}
