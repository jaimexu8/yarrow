'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { ExtractedContentList } from '@/components/viewer/ExtractedContentList';
import { OriginalPane } from '@/components/viewer/OriginalPane';
import { SplitViewer } from '@/components/viewer/SplitViewer';
import { ViewerHeader } from '@/components/viewer/ViewerHeader';
import {
  ViewerProvider,
  type ViewMode,
} from '@/components/viewer/ViewerContext';
import { toApiError } from '@/lib/errors';
import { getDocumentTree, type DocumentTree } from '@/lib/viewer';

const IN_PROGRESS = new Set(['queued', 'processing']);
const POLL_INTERVAL_MS = 5000;

type Load =
  | { state: 'loading' }
  | { state: 'ready'; tree: DocumentTree }
  | { state: 'missing' }
  | { state: 'error'; message: string };

/** Side by side where there is room; one pane at a time on a phone. */
function defaultMode(): ViewMode {
  return window.matchMedia('(min-width: 768px)').matches ? 'split' : 'original';
}

/**
 * View one document (US-12): the original on the left, the extracted text
 * on the right, with a toggle to show either alone. While the document is
 * still processing, the extracted side fills in once it finishes.
 */
export default function DocumentViewerPage({
  params,
}: {
  params: { id: string };
}) {
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function fetchTree() {
      try {
        const tree = await getDocumentTree(params.id);
        if (!current) return;
        setLoad({ state: 'ready', tree });
        if (tree.document.status && IN_PROGRESS.has(tree.document.status)) {
          timer = setTimeout(fetchTree, POLL_INTERVAL_MS);
        }
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
      clearTimeout(timer);
    };
  }, [params.id, attempt]);

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

  return (
    <ViewerProvider
      document={load.tree.document}
      tree={load.tree}
      initialMode={defaultMode()}
    >
      <div className="flex h-full flex-col">
        <ViewerHeader />
        <SplitViewer
          original={<OriginalPane />}
          extracted={<ExtractedContentList />}
        />
      </div>
    </ViewerProvider>
  );
}
