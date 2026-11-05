'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useState, use } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { ExtractedContentList } from '@/components/viewer/ExtractedContentList';
import { OriginalPane } from '@/components/viewer/OriginalPane';
import { ProcessingProgressBar } from '@/components/viewer/ProcessingProgressBar';
import {
  ReprocessBar,
  ReprocessButton,
} from '@/components/viewer/ReprocessBar';
import { BoundingBoxCanvas } from '@/components/viewer/BoundingBoxCanvas';
import { SplitViewer } from '@/components/viewer/SplitViewer';
import { KeyboardShortcutsModal } from '@/components/viewer/KeyboardShortcutsModal';
import { ThumbnailSidebar } from '@/components/viewer/ThumbnailSidebar';
import { useViewerShortcuts } from '@/components/viewer/useViewerShortcuts';
import { ViewerHeader } from '@/components/viewer/ViewerHeader';
import {
  ViewerProvider,
  useViewer,
  type ViewMode,
} from '@/components/viewer/ViewerContext';
import { isWorkingOn } from '@/lib/documents';
import { toApiError } from '@/lib/errors';
import { useDocumentProgress } from '@/lib/useDocumentProgress';
import { getDocumentTree, type DocumentTree } from '@/lib/viewer';
import { TableOperationsDropdown } from '@/components/viewer/TableOperationsMenu';
import { ExportMenu } from '@/components/viewer/ExportMenu';

type Load =
  | { state: 'loading' }
  | { state: 'ready'; tree: DocumentTree }
  | { state: 'missing' }
  | { state: 'error'; message: string };

/** Side by side where there is room; one pane at a time on a phone. */
function defaultMode(): ViewMode {
  return window.matchMedia('(min-width: 768px)').matches ? 'split' : 'original';
}

function DocumentViewer({ documentId }: { documentId: string }) {
  const searchParams = useSearchParams();
  const regionId = searchParams.get('region');
  const query = searchParams.get('q') ?? '';
  const cellId = searchParams.get('cell');

  const requestedPage = Number(searchParams.get('page'));
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;

    async function fetchTree() {
      try {
        const tree = await getDocumentTree(documentId);
        if (!current) return;
        setLoad({ state: 'ready', tree });
      } catch (err) {
        if (!current) return;
        const apiError = toApiError(err);
        setLoad((previous) =>
          // Keep showing a document already on screen through a failed poll.
          previous.state === 'ready'
            ? previous
            : apiError.status === 404 || apiError.status === 422
              ? { state: 'missing' }
              : { state: 'error', message: apiError.message }
        );
      }
    }

    fetchTree();
    return () => {
      current = false;
    };
  }, [documentId, attempt]);

  // Fetch the document again, for instance, after starting a reprocess.
  const refresh = useCallback(() => setAttempt((count) => count + 1), []);
  const working = load.state === 'ready' && isWorkingOn(load.tree.document);
  const progress = useDocumentProgress(documentId, working);

  if (load.state === 'loading') {
    return (
      <div className="flex h-full items-center justify-center text-slate-500">
        <Spinner label="Loading the document" />
      </div>
    );
  }

  if (load.state === 'missing' || load.state === 'error') {
    return (
      <div className="mx-auto max-w-md space-y-4 px-4 py-16 text-center">
        {load.state === 'missing' ? (
          <>
            <h1 className="text-lg font-semibold text-slate-900">
              Document not found
            </h1>
            <p className="text-sm text-slate-600">
              It may have been deleted, or the link is wrong.
            </p>
          </>
        ) : (
          <>
            <h1 className="text-lg font-semibold text-slate-900">
              Couldn&apos;t open this document
            </h1>
            <Alert tone="error">{load.message}</Alert>
            <Button
              variant="secondary"
              className="w-auto"
              onClick={() => {
                setLoad({ state: 'loading' });
                setAttempt((count) => count + 1);
              }}
            >
              Try again
            </Button>
          </>
        )}
        <p>
          <Link
            href="/dashboard"
            className="text-sm font-medium text-slate-900 underline underline-offset-4"
          >
            Back to documents
          </Link>
        </p>
      </div>
    );
  }

  // A page outside the document (a stale link) opens at the first page
  const pageCount = load.tree.document.page_count ?? load.tree.stats.page_count;
  const initialPage =
    Number.isInteger(requestedPage) &&
    requestedPage >= 1 &&
    requestedPage <= pageCount
      ? requestedPage
      : 1;

  return (
    <ViewerProvider
      document={load.tree.document}
      tree={load.tree}
      progress={working ? progress : null}
      initialMode={defaultMode()}
      initialPage={initialPage}
      refresh={refresh}
      highlightRegionId={regionId}
      highlightQuery={query}
      highlightCellId={cellId}
    >
      <DocumentViewerLayout refresh={refresh} />
    </ViewerProvider>
  );
}

function DocumentViewerLayout({ refresh }: { refresh: () => void }) {
  const { showBoundingBoxes, shortcutsHelpOpen, setShortcutsHelpOpen } =
    useViewer();
  const announcement = useViewerShortcuts();
  return (
    <div className="flex h-full flex-col">
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>
      <KeyboardShortcutsModal
        open={shortcutsHelpOpen}
        onClose={() => setShortcutsHelpOpen(false)}
      />
      <ViewerHeader
        actions={
          <>
            <ReprocessButton onReprocessed={refresh} />
            <TableOperationsDropdown onDone={refresh} />
            <ExportMenu />
          </>
        }
      />
      <ProcessingProgressBar />
      <ReprocessBar onReprocessed={refresh} />
      <div className="flex min-h-0 flex-1">
        <ThumbnailSidebar />
        <SplitViewer
          original={
            <OriginalPane
              // Rendered as an element, not called, so BoundingBoxCanvas's hooks
              // belong to it rather than to the PageFrame that draws it
              renderPageOverlay={
                showBoundingBoxes
                  ? (info) => <BoundingBoxCanvas {...info} />
                  : undefined
              }
            />
          }
          extracted={<ExtractedContentList />}
        />
      </div>
    </div>
  );
}

export default function DocumentViewerPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  // Next 15+ passes `params` as a Promise; this page is a client component,
  // so unwrap it with use() before rendering.
  const { id } = use(params);
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center text-slate-500">
          <Spinner label="Loading the document" />
        </div>
      }
    >
      <DocumentViewer documentId={id} />
    </Suspense>
  );
}
