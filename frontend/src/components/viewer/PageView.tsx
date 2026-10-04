'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import { Spinner } from '@/components/ui/Spinner';
import { cn } from '@/lib/cn';
import type { PageNode } from '@/lib/viewer';
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
  const { tree, registerPage } = useViewer();
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

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
        task = page.render({ canvasContext: context, viewport });
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
