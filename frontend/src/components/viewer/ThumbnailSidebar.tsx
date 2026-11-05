'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { cn } from '@/lib/cn';
import { isWorkingOn } from '@/lib/documents';
import {
  cachedPageThumbnail,
  loadPageThumbnail,
  revokePageThumbnails,
} from '@/lib/thumbnails';
import type { PageNode } from '@/lib/viewer';
import { useViewer } from './ViewerContext';

export const THUMBNAIL_SIDEBAR_ID = 'viewer-thumbnails';

const LAZY_LOAD_MARGIN = '200px';
const LETTER_ASPECT_RATIO = 8.5 / 11;

function useNearViewport(
  element: HTMLElement | null,
  root: HTMLElement | null
): boolean {
  const [near, setNear] = useState(false);
  const observable = typeof IntersectionObserver !== 'undefined';
  useEffect(() => {
    if (!element || near || !observable) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setNear(true);
      },
      { root, rootMargin: LAZY_LOAD_MARGIN }
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [element, root, near, observable]);
  return near || !observable;
}

function PageThumbnail({
  documentId,
  pageNumber,
  node,
  fetchable,
  retryToken,
  active,
  root,
  onSelect,
}: {
  documentId: string;
  pageNumber: number;
  node: PageNode | undefined;
  fetchable: boolean;
  retryToken: string;
  active: boolean;
  root: HTMLElement | null;
  onSelect: (page: number) => void;
}) {
  const [button, setButton] = useState<HTMLButtonElement | null>(null);
  const near = useNearViewport(button, root);
  const [url, setUrl] = useState(() =>
    cachedPageThumbnail(documentId, pageNumber)
  );
  const [failedToken, setFailedToken] = useState<string | null>(null);

  useEffect(() => {
    if (!near || !fetchable || url || failedToken === retryToken) return;
    let current = true;
    loadPageThumbnail(documentId, pageNumber).then(
      (loaded) => {
        if (current) setUrl(loaded);
      },
      () => {
        if (current) setFailedToken(retryToken);
      }
    );
    return () => {
      current = false;
    };
  }, [near, fetchable, url, failedToken, retryToken, documentId, pageNumber]);

  const aspectRatio =
    node?.width && node?.height
      ? node.width / node.height
      : LETTER_ASPECT_RATIO;
  const showImage = fetchable && url !== null;

  return (
    <button
      ref={setButton}
      type="button"
      aria-current={active ? 'page' : undefined}
      aria-label={`Page ${pageNumber}`}
      onClick={() => onSelect(pageNumber)}
      className={cn(
        'flex w-full flex-col items-center gap-1.5 rounded-md p-1.5 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900',
        active ? 'bg-white ring-2 ring-slate-900' : 'hover:bg-slate-100'
      )}
    >
      <span
        className="block w-full overflow-hidden rounded-sm border border-slate-200 bg-white shadow-sm"
        style={showImage ? undefined : { aspectRatio }}
      >
        {showImage ? (
          <img
            src={url}
            alt=""
            draggable={false}
            className="block h-auto w-full"
          />
        ) : (
          <span
            data-testid="thumbnail-placeholder"
            aria-hidden="true"
            className="flex h-full w-full items-center justify-center text-lg font-medium text-slate-300 tabular-nums"
          >
            {pageNumber}
          </span>
        )}
      </span>
      <span
        aria-hidden="true"
        className={cn(
          'text-xs tabular-nums',
          active ? 'font-semibold text-slate-900' : 'text-slate-500'
        )}
      >
        {pageNumber}
      </span>
    </button>
  );
}

export function ThumbnailSidebar() {
  const {
    document,
    tree,
    pageCount,
    currentPage,
    scrollToPage,
    thumbnailsOpen,
  } = useViewer();
  const [root, setRoot] = useState<HTMLElement | null>(null);
  const list = useRef<HTMLOListElement>(null);

  useEffect(() => () => revokePageThumbnails(), []);

  useEffect(() => {
    if (!thumbnailsOpen) return;
    list.current
      ?.querySelector<HTMLElement>('[aria-current="page"]')
      ?.scrollIntoView({ block: 'nearest' });
  }, [currentPage, thumbnailsOpen]);

  const nodes = useMemo(
    () => new Map(tree?.pages.map((page) => [page.page_number, page]) ?? []),
    [tree]
  );
  const working = isWorkingOn(document);
  const retryToken = tree?.generated_at ?? '';
  const pages = Array.from({ length: pageCount ?? 0 }, (_, index) => index + 1);

  return (
    <nav
      ref={setRoot}
      id={THUMBNAIL_SIDEBAR_ID}
      aria-label="Page thumbnails"
      hidden={!thumbnailsOpen}
      className="w-44 shrink-0 overflow-y-auto border-r border-slate-200 bg-slate-50"
    >
      <ol ref={list} className="space-y-2 p-3">
        {pages.map((pageNumber) => {
          const node = nodes.get(pageNumber);
          return (
            <li key={pageNumber}>
              <PageThumbnail
                documentId={document.id}
                pageNumber={pageNumber}
                node={node}
                fetchable={node ? node.has_thumbnail : working}
                retryToken={retryToken}
                active={pageNumber === currentPage}
                root={root}
                onSelect={scrollToPage}
              />
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
