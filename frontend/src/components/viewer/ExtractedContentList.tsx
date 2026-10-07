'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, FileSearch, Merge } from 'lucide-react';
import { Spinner } from '@/components/ui/Spinner';
import { isInterrupted } from '@/lib/documents';
import { toApiError } from '@/lib/errors';
import {
  getMergeCandidates,
  mergeTablePair,
  type MergeCandidate,
  type MergeCandidates,
} from '@/lib/tableOperations';
import type { DocumentTree, PageNode, TableNode } from '@/lib/viewer';
import { RegionBlock, regionKind } from './regionRenderers';
import { usePageTracking, useViewer } from './ViewerContext';

/**
 * Which region each logical table is shown at: the first of its parts that
 * is in this tree, so a table that continues across pages appears once,
 * where it starts.
 */
function tablePlacements(tree: DocumentTree): Map<string, TableNode> {
  const present = new Set(
    tree.pages.flatMap((page) =>
      page.regions.flatMap((region) =>
        region.region_table_id ? [region.region_table_id] : []
      )
    )
  );
  const placements = new Map<string, TableNode>();
  for (const table of tree.tables) {
    const part = table.parts.find((candidate) =>
      present.has(candidate.region_table_id)
    );
    if (part) placements.set(part.region_table_id, table);
  }
  return placements;
}

/**
 * The divider above a page. Where the table that ends the previous page can
 * be joined to the one that starts this page, it carries a button that
 * merges them.
 */
function PageBoundary({
  pageNumber,
  candidate,
}: {
  pageNumber: number;
  candidate?: MergeCandidate;
}) {
  const { document, refresh } = useViewer();
  const [merging, setMerging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!candidate) {
    return (
      <hr aria-hidden="true" className="not-prose my-8 border-slate-200" />
    );
  }

  async function merge() {
    if (!candidate) return;
    setMerging(true);
    setError(null);
    try {
      await mergeTablePair(
        document.id,
        candidate.previous_table_id,
        candidate.next_table_id
      );
      // The reload replaces this boundary (it is keyed by its candidate), so
      // the spinner is not reset here; that would only flicker.
      refresh();
    } catch (err) {
      setError(toApiError(err).message);
      setMerging(false);
    }
  }

  return (
    <div className="not-prose my-8">
      <div className="flex items-center gap-3">
        <span aria-hidden="true" className="h-px flex-1 bg-slate-200" />
        <button
          type="button"
          onClick={merge}
          disabled={merging}
          aria-label={`Merge the table on page ${pageNumber - 1} with the table on page ${pageNumber}`}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-700 shadow-sm transition-colors hover:border-slate-300 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed disabled:text-slate-400"
        >
          {merging ? (
            <Spinner label={null} className="size-3.5" />
          ) : (
            <Merge aria-hidden="true" className="size-3.5" />
          )}
          {merging ? 'Merging…' : 'Merge tables across this page break'}
        </button>
        <span aria-hidden="true" className="h-px flex-1 bg-slate-200" />
      </div>
      {error && (
        <p role="alert" className="mt-2 text-center text-xs text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}

// A page whose content is not available yet
type Pending = 'processing' | 'unfinished';

function PageSection({
  pageNumber,
  page,
  placements,
  pending,
  mergeCandidate,
}: {
  pageNumber: number;
  page: PageNode | undefined;
  placements: Map<string, TableNode>;
  pending?: Pending;
  mergeCandidate?: MergeCandidate;
}) {
  const { registerPage } = useViewer();
  const regions = [...(page?.regions ?? [])].sort(
    (a, b) => a.reading_order - b.reading_order
  );

  return (
    <section
      ref={(element) => registerPage('extracted', pageNumber, element)}
      data-viewer-page={pageNumber}
      data-pane="extracted"
      aria-labelledby={`extracted-page-${pageNumber}`}
    >
      <h2 id={`extracted-page-${pageNumber}`} className="sr-only">
        Page {pageNumber}
      </h2>
      {pageNumber > 1 && (
        <PageBoundary
          // A new candidate gets a fresh boundary, so a finished merge's
          // spinner does not carry over to it
          key={
            mergeCandidate
              ? `${mergeCandidate.previous_table_id}:${mergeCandidate.next_table_id}`
              : 'none'
          }
          pageNumber={pageNumber}
          candidate={mergeCandidate}
        />
      )}

      {pending ? (
        <p className="not-prose my-4 flex items-center gap-2 text-sm text-slate-500">
          {pending === 'processing' ? (
            <>
              <Spinner label={null} className="text-slate-400" />
              Processing this page…
            </>
          ) : (
            "This page hasn't been processed yet."
          )}
        </p>
      ) : (
        <PageContent page={page} regions={regions} placements={placements} />
      )}
    </section>
  );
}

function PageContent({
  page,
  regions,
  placements,
}: {
  page: PageNode | undefined;
  regions: PageNode['regions'];
  placements: Map<string, TableNode>;
}) {
  return (
    <>
      {page?.error_message && (
        <p className="not-prose my-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <AlertTriangle
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0"
          />
          {page.error_message}
        </p>
      )}

      {regions.map((region, index) => {
        const previous = regions[index - 1];
        return (
          <RegionBlock
            key={region.id}
            region={region}
            context={{
              table: region.region_table_id
                ? (placements.get(region.region_table_id) ?? null)
                : null,
              captioned: previous ? regionKind(previous) === 'caption' : false,
            }}
          />
        );
      })}
    </>
  );
}

function PaneMessage({
  children,
  busy = false,
}: {
  children: React.ReactNode;
  busy?: boolean;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center text-sm text-slate-500">
      {busy ? (
        <Spinner label={null} className="text-slate-400" />
      ) : (
        <FileSearch aria-hidden="true" className="size-5 text-slate-400" />
      )}
      <p className="max-w-xs">{children}</p>
    </div>
  );
}

/**
 * The extracted document styled like rendered Markdown. Built from the
 * parsed tree so every block keeps its region id (see RegionBlock).
 */
export function ExtractedContentList() {
  const {
    document,
    tree,
    pageCount,
    highlightRegionId,
    highlightQuery,
    scrollToPage,
  } = useViewer();
  const onScroll = usePageTracking('extracted');

  // Which tables can be merged or split
  const [tableEdits, setTableEdits] = useState<MergeCandidates | null>(null);

  // Map of merge candidates by their boundary page for quick lookup
  const mergeCandidateByPage = useMemo(
    () =>
      new Map(
        (tableEdits?.candidates ?? []).map((c) => [c.boundary_page + 1, c])
      ),
    [tableEdits]
  );

  useEffect(() => {
    if (document.status !== 'completed' || !tree) {
      setTableEdits(null);
      return;
    }

    let current = true;

    getMergeCandidates(document.id)
      .then((found) => {
        if (current) setTableEdits(found);
      })
      .catch(() => {
        if (current) setTableEdits(null);
      });

    return () => {
      current = false;
    };
  }, [document.id, document.status, tree]);

  const editable = tableEdits?.can_edit ?? false;

  const placements = useMemo(
    () => (tree ? tablePlacements(tree) : new Map<string, TableNode>()),
    [tree]
  );

  useEffect(() => {
    if (!highlightRegionId || !tree) return;
    const region = tree.pages
      .flatMap((page) => page.regions)
      .find((item) => item.id === highlightRegionId);
    if (!region) return;
    scrollToPage(region.page_number);
    const timer = window.setTimeout(() => {
      window.document
        .getElementById(`region-${highlightRegionId}`)
        ?.scrollIntoView({
          block: 'center',
          behavior: 'smooth',
        });
    }, 50);
    return () => window.clearTimeout(timer);
  }, [tree, highlightRegionId, highlightQuery, scrollToPage]);

  // Determine if the document is currently in progress (queued or processing)
  const inProgress =
    document.status === 'queued' || document.status === 'processing';

  // Determine which pages have been finished
  const finished = new Set(
    (tree?.pages ?? [])
      .filter((page) => page.status === 'completed')
      .map((page) => page.page_number)
  );

  let body: React.ReactNode;
  if (inProgress && finished.size === 0) {
    body = isInterrupted(document) ? (
      <PaneMessage>
        Processing stopped before any page was finished.
      </PaneMessage>
    ) : (
      <PaneMessage busy>
        Still processing. The extracted text will appear here when it&apos;s
        ready.
      </PaneMessage>
    );
  } else if (inProgress && tree) {
    const pending: Pending = isInterrupted(document)
      ? 'unfinished'
      : 'processing';
    const byNumber = new Map(
      tree.pages.map((page) => [page.page_number, page])
    );
    const lastPage = Math.max(
      pageCount ?? 0,
      ...tree.pages.map((p) => p.page_number)
    );
    body = (
      <article className="prose prose-slate prose-sm mx-auto max-w-2xl px-6 py-8 sm:px-10 2xl:prose-base">
        {Array.from({ length: lastPage }, (_, index) => index + 1).map(
          (number) => (
            <PageSection
              key={number}
              pageNumber={number}
              page={byNumber.get(number)}
              placements={placements}
              pending={finished.has(number) ? undefined : pending}
              mergeCandidate={mergeCandidateByPage.get(number)}
            />
          )
        )}
      </article>
    );
  } else if (!tree) {
    body = (
      <PaneMessage busy>
        <span className="sr-only">Loading the extracted text</span>
      </PaneMessage>
    );
  } else if (
    tree.stats.region_count === 0 &&
    !tree.pages.some((p) => p.error_message)
  ) {
    body = (
      <PaneMessage>
        {document.status === 'completed'
          ? 'No text was found in this document.'
          : document.error_message ||
            'There is no extracted text for this document.'}
      </PaneMessage>
    );
  } else {
    const pages = [...tree.pages].sort((a, b) => a.page_number - b.page_number);
    body = (
      <article className="prose prose-slate prose-sm mx-auto max-w-2xl px-6 py-8 sm:px-10 2xl:prose-base">
        {pages.map((page) => (
          <PageSection
            key={page.page_number}
            pageNumber={page.page_number}
            page={page}
            placements={placements}
            mergeCandidate={mergeCandidateByPage.get(page.page_number)}
          />
        ))}
      </article>
    );
  }

  return (
    <div
      data-viewer-scroll
      onScroll={onScroll}
      className="h-full overflow-y-auto bg-white"
    >
      <h2 className="sr-only">Extracted text</h2>
      {body}
    </div>
  );
}
