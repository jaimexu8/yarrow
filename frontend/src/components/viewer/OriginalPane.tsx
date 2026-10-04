'use client';

import { isAxiosError } from 'axios';
import { useEffect, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { toApiError } from '@/lib/errors';
import { getOriginalFile } from '@/lib/viewer';
import { PageFrame, PdfPageView, type RenderPageOverlay } from './PageView';
import { usePageTracking, useViewer } from './ViewerContext';

type Original =
  | { kind: 'pdf'; pdf: PDFDocumentProxy; aspectRatios: number[] }
  | { kind: 'image'; url: string; aspectRatio: number };

// Default page aspect ratio (letter size: 8.5 x 11 inches)
const DEFAULT_ASPECT = 8.5 / 11;

async function openPdf(data: ArrayBuffer): Promise<Original> {
  const { default: pdfjs } = await import('@/lib/pdf');
  const pdf = await pdfjs.getDocument({ data: new Uint8Array(data.slice(0)) })
    .promise;

  const aspectRatios = await Promise.all(
    Array.from({ length: pdf.numPages }, async (_, index) => {
      const viewport = (await pdf.getPage(index + 1)).getViewport({ scale: 1 });
      return viewport.width / viewport.height;
    })
  );
  return { kind: 'pdf', pdf, aspectRatios };
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
  const { document, setPageCount } = useViewer();
  const onScroll = usePageTracking('original');
  const [original, setOriginal] = useState<Original | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    let objectUrl: string | null = null;
    let pdf: PDFDocumentProxy | null = null;
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
        if (opened.kind === 'pdf') pdf = opened.pdf;
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
      pdf?.destroy();
    };
  }, [document.id, document.file_type, setPageCount, attempt]);

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
          {original.kind === 'pdf' ? (
            original.aspectRatios.map((aspectRatio, index) => (
              <PdfPageView
                key={index}
                pdf={original.pdf}
                pageNumber={index + 1}
                aspectRatio={aspectRatio}
                renderOverlay={renderPageOverlay}
              />
            ))
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
          )}
        </div>
      )}
    </div>
  );
}
