'use client';

import { useMemo } from 'react';
import { AlertTriangle, FileSearch } from 'lucide-react';
import { Spinner } from '@/components/ui/Spinner';
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

function PageSection({
  page,
  placements,
}: {
  page: PageNode;
  placements: Map<string, TableNode>;
}) {
  const { registerPage } = useViewer();
  const regions = [...page.regions].sort(
    (a, b) => a.reading_order - b.reading_order
  );

  return (
    <section
      ref={(element) => registerPage('extracted', page.page_number, element)}
      data-viewer-page={page.page_number}
      data-pane="extracted"
      aria-labelledby={`extracted-page-${page.page_number}`}
    >
      <h2 id={`extracted-page-${page.page_number}`} className="sr-only">
        Page {page.page_number}
      </h2>
      {page.page_number > 1 && (
        <hr aria-hidden="true" className="not-prose my-8 border-slate-200" />
      )}

      {page.error_message && (
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
    </section>
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
  const { document, tree } = useViewer();
  const onScroll = usePageTracking('extracted');
  const placements = useMemo(
    () => (tree ? tablePlacements(tree) : new Map<string, TableNode>()),
    [tree]
  );

  let body: React.ReactNode;
  if (document.status === 'queued' || document.status === 'processing') {
    body = (
      <PaneMessage busy>
        Still processing. The extracted text will appear here when it&apos;s
        ready.
      </PaneMessage>
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
            page={page}
            placements={placements}
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
