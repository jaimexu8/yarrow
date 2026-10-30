'use client';

import { isAxiosError } from 'axios';
import { useEffect, useState } from 'react';
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from 'pdfjs-dist';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { toApiError } from '@/lib/errors';
import { getOriginalFile } from '@/lib/viewer';
import {
  CorrectedPageView,
  PageFrame,
  PdfPageView,
  OriginalToggle,
  type RenderPageOverlay,
} from './PageView';
import { usePageTracking, useViewer } from './ViewerContext';

type Original =
  | {
      kind: 'pdf';
      pdf: PDFDocumentProxy;
      // The loading task owns the worker; pdf.js v6 removed
      // PDFDocumentProxy.destroy(), so cleanup goes through task.destroy().
      task: PDFDocumentLoadingTask;
      aspectRatios: number[];
    }
  | { kind: 'image'; url: string; aspectRatio: number };

// Default page aspect ratio (letter size: 8.5 x 11 inches)
const DEFAULT_ASPECT = 8.5 / 11;

async function openPdf(data: ArrayBuffer): Promise<Original> {
  const { default: pdfjs } = await import('@/lib/pdf');
  const task = pdfjs.getDocument({ data: new Uint8Array(data.slice(0)) });
  const pdf = await task.promise;

  const aspectRatios = await Promise.all(
    Array.from({ length: pdf.numPages }, async (_, index) => {
      const viewport = (await pdf.getPage(index + 1)).getViewport({ scale: 1 });
      return viewport.width / viewport.height;
    })
  );
  return { kind: 'pdf', pdf, task, aspectRatios };
}

function imageAspect(url: string): Promise<number> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () =>
      resolve(image.naturalWidth / image.naturalHeight || DEFAULT_ASPECT);
    image.onerror = () => resolve(DEFAULT_ASPECT);
    image.src = url;
  });
}

/**
 * The original uploaded file as the user sent it
 */
export function OriginalPane({
  renderPageOverlay,
}: {
  renderPageOverlay?: RenderPageOverlay;
}) {
  const {
    document,
    setPageCount,
    initialPage,
    scrollToPage,
    tree,
    originalPages,
    toggleOriginalPage,
  } = useViewer();
  const onScroll = usePageTracking('original');
  const [original, setOriginal] = useState<Original | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    let objectUrl: string | null = null;
    let task: PDFDocumentLoadingTask | null = null;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Intentional reset-before-load: the async block fetches and parses the file; async callbacks apply result or error.
    setError(null);

    (async () => {
      try {
        const file = await getOriginalFile(document.id);
        if (!current) return;
        const type = file.contentType || document.file_type;
        if (type.startsWith('image/')) {
          objectUrl = URL.createObjectURL(new Blob([file.data], { type }));
          const aspectRatio = await imageAspect(objectUrl);
          if (current)
            setOriginal({ kind: 'image', url: objectUrl, aspectRatio });
          return;
        }
        const opened = await openPdf(file.data);
        if (opened.kind === 'pdf') task = opened.task;
        if (!current) return;
        setOriginal(opened);
        if (opened.kind === 'pdf') setPageCount(opened.pdf.numPages);
      } catch (err) {
        if (!current) return;
        setError(
          isAxiosError(err)
            ? toApiError(err).message
            : 'The original file could not be opened.' // The file downloaded but pdf.js could not read it.
        );
      }
    })();

    return () => {
      current = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      task?.destroy();
    };
  }, [document.id, document.file_type, setPageCount, attempt]);

  // Opened from a ?page= link
  useEffect(() => {
    if (!original || initialPage <= 1) {
      return;
    }

    scrollToPage(initialPage, 'original');
  }, [original, initialPage, scrollToPage]);

  return (
    <div
      data-viewer-scroll
      onScroll={onScroll}
      className="h-full overflow-y-auto bg-slate-100 px-4 py-6 sm:px-6"
    >
      <h2 className="sr-only">Original document</h2>
      {error ? (
        <div className="mx-auto max-w-md space-y-3 pt-10 text-center">
          <Alert tone="error">{error}</Alert>
          <Button
            variant="secondary"
            className="w-auto"
            onClick={() => {
              setOriginal(null);
              setAttempt((count) => count + 1);
            }}
          >
            Try again
          </Button>
        </div>
      ) : !original ? (
        <div className="flex h-full items-center justify-center text-slate-500">
          <Spinner label="Loading the original document" />
        </div>
      ) : (
        <div className="mx-auto max-w-3xl space-y-6">
          {original.kind === 'pdf'
            ? original.aspectRatios.map((aspectRatio, index) => {
                const pageNumber = index + 1;
                const parsedPage =
                  tree?.pages.find((page) => page.page_number === pageNumber) ??
                  null;
                // A rotated page shows its corrected upright image by default,
                // with a toggle back to the raw original.
                const isCorrected =
                  !!parsedPage?.image_key && !originalPages.has(pageNumber);
                const pageView = isCorrected ? (
                  <CorrectedPageView
                    documentId={document.id}
                    pageNumber={pageNumber}
                    fallbackAspectRatio={aspectRatio}
                    renderOverlay={renderPageOverlay}
                  />
                ) : (
                  <PdfPageView
                    pdf={original.pdf}
                    pageNumber={pageNumber}
                    aspectRatio={aspectRatio}
                    renderOverlay={renderPageOverlay}
                  />
                );
                return (
                  <div key={index} className="relative">
                    {pageView}
                    {parsedPage?.image_key && (
                      <OriginalToggle
                        label={
                          isCorrected
                            ? 'Auto-rotated: show original'
                            : 'Showing original: show corrected'
                        }
                        onClick={() => toggleOriginalPage(pageNumber)}
                      />
                    )}
                  </div>
                );
              })
            : (() => {
                const parsedPage =
                  tree?.pages.find((page) => page.page_number === 1) ?? null;
                const isCorrected =
                  !!parsedPage?.image_key && !originalPages.has(1);
                const pageView = isCorrected ? (
                  <CorrectedPageView
                    documentId={document.id}
                    pageNumber={1}
                    fallbackAspectRatio={original.aspectRatio}
                    renderOverlay={renderPageOverlay}
                  />
                ) : (
                  <PageFrame
                    pageNumber={1}
                    aspectRatio={original.aspectRatio}
                    renderOverlay={renderPageOverlay}
                  >
                    <img
                      src={original.url}
                      alt={`Original of ${document.filename}`}
                      className="absolute inset-0 size-full object-contain"
                    />
                  </PageFrame>
                );
                return (
                  <div className="relative">
                    {pageView}
                    {parsedPage?.image_key && (
                      <OriginalToggle
                        label={
                          isCorrected
                            ? 'Auto-rotated: show original'
                            : 'Showing original: show corrected'
                        }
                        onClick={() => toggleOriginalPage(1)}
                      />
                    )}
                  </div>
                );
              })()}
        </div>
      )}
    </div>
  );
}
