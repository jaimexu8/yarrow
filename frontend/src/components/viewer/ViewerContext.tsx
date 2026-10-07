'use client';

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { DocumentSummary } from '@/lib/documents';
import type { DocumentTree } from '@/lib/viewer';

export type ViewMode = 'original' | 'split' | 'extracted';

// Margin left alone on top of a page when navigating to it
const PAGE_SCROLL_MARGIN = 16;
export type Pane = 'original' | 'extracted';

type ViewerState = {
  document: DocumentSummary;

  // Null until the parsed content has loaded.
  tree: DocumentTree | null;

  // Page count in the document, once known from either the file or the tree.
  pageCount: number | null;
  setPageCount: (count: number) => void;
  mode: ViewMode;
  setMode: (mode: ViewMode) => void;

  // The page the reader is on, from whichever pane they last scrolled.
  currentPage: number;

  // Bring a page to the top of every visible pane.
  scrollToPage: (page: number, pane?: Pane) => void;

  // Panes call these to make their pages reachable by scrollToPage.
  registerPage: (pane: Pane, page: number, element: HTMLElement | null) => void;
  // Returns true only when the page differs from the one already current.
  reportVisiblePage: (page: number) => boolean;

  // current find hit; starts from a /search link's ?region=&q=
  highlightRegionId: string | null;
  highlightQuery: string;
  setHighlight: (regionId: string | null, query: string) => void;
};

/*
 * Shared state for everything inside the viewer
 */
const ViewerContext = createContext<ViewerState | null>(null);

export function ViewerProvider({
  document,
  tree,
  initialMode = 'split',
  highlightRegionId: initialHighlightRegionId = null,
  highlightQuery: initialHighlightQuery = '',
  children,
}: {
  document: DocumentSummary;
  tree: DocumentTree | null;
  initialMode?: ViewMode;
  highlightRegionId?: string | null;
  highlightQuery?: string;
  children: ReactNode;
}) {
  const [mode, setMode] = useState<ViewMode>(initialMode);
  const [pageCount, setPageCount] = useState<number | null>(
    document.page_count
  );
  const [currentPage, setCurrentPage] = useState(1);
  const [highlightRegionId, setHighlightRegionId] = useState<string | null>(
    initialHighlightRegionId
  );
  const [highlightQuery, setHighlightQuery] = useState(initialHighlightQuery);

  const setHighlight = useCallback((regionId: string | null, query: string) => {
    setHighlightRegionId(regionId);
    setHighlightQuery(query);
  }, []);

  // Read synchronously by scroll handlers, which can fire before a re-render
  const currentPageRef = useRef(1);

  const anchors = useRef<Record<Pane, Map<number, HTMLElement>>>({
    original: new Map(),
    extracted: new Map(),
  });

  const registerPage = useCallback(
    (pane: Pane, page: number, element: HTMLElement | null) => {
      if (element) anchors.current[pane].set(page, element);
      else anchors.current[pane].delete(page);
    },
    []
  );

  const scrollToPage = useCallback((page: number, pane?: Pane) => {
    currentPageRef.current = page;
    setCurrentPage(page);
    for (const p of ['original', 'extracted'] as const) {
      if (pane && pane !== p) continue;
      const element = anchors.current[p].get(page);
      const container = element?.closest<HTMLElement>('[data-viewer-scroll]');

      // If a pane is hidden, continue
      if (!element?.offsetParent || !container) continue;

      container.scrollTop +=
        element.getBoundingClientRect().top -
        container.getBoundingClientRect().top -
        PAGE_SCROLL_MARGIN;
    }
  }, []);

  const reportVisiblePage = useCallback((page: number) => {
    if (page === currentPageRef.current) return false;
    currentPageRef.current = page;
    setCurrentPage(page);
    return true;
  }, []);

  const value = useMemo<ViewerState>(
    () => ({
      document,
      tree,
      pageCount: pageCount ?? tree?.stats.page_count ?? null,
      setPageCount,
      mode,
      setMode,
      currentPage,
      scrollToPage,
      registerPage,
      reportVisiblePage,
      highlightRegionId,
      highlightQuery,
      setHighlight,
    }),
    [
      document,

      tree,

      pageCount,

      mode,

      currentPage,

      scrollToPage,

      registerPage,
      reportVisiblePage,
      ,
      highlightRegionId,
      highlightQuery,
      setHighlight,
    ]
  );

  return (
    <ViewerContext.Provider value={value}>{children}</ViewerContext.Provider>
  );
}

export function useViewer(): ViewerState {
  const context = useContext(ViewerContext);
  if (!context)
    throw new Error('useViewer must be used within a ViewerProvider');
  return context;
}

/**
 * Keeps currentPage in step with a scrolling pane. The page whose top edge
 * most recently passed the upper third of the pane is the current one.
 * Returns an onScroll handler for the pane's scroll container.
 */
export function usePageTracking(pane: Pane) {
  const { reportVisiblePage, scrollToPage } = useViewer();
  return useCallback(
    (event: React.UIEvent<HTMLElement>) => {
      const container = event.currentTarget;

      // Hiding a pane resets its scroll and fires this with every page at
      // the top, which would read as the last page
      if (!container.offsetParent) return;

      const line =
        container.getBoundingClientRect().top + container.clientHeight / 3;
      let page: number | null = null;
      for (const element of Array.from(
        container.querySelectorAll<HTMLElement>(
          `[data-viewer-page][data-pane="${pane}"]`
        )
      )) {
        if (element.getBoundingClientRect().top <= line) {
          page = Number(element.dataset.viewerPage);
        } else {
          break;
        }
      }
      if (page !== null && reportVisiblePage(page)) {
        scrollToPage(page, pane === 'original' ? 'extracted' : 'original');
      }
    },
    [pane, reportVisiblePage, scrollToPage]
  );
}
