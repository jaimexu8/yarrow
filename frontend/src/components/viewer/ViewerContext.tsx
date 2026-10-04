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
  scrollToPage: (page: number) => void;

  // Panes call these to make their pages reachable by scrollToPage.
  registerPage: (pane: Pane, page: number, element: HTMLElement | null) => void;
  reportVisiblePage: (page: number) => void;
  /** Load the document again, e.g. after merging or splitting its tables. */
  refresh: () => void;
};

/*
 * Shared state for everything inside the viewer
 */
const ViewerContext = createContext<ViewerState | null>(null);

export function ViewerProvider({
  document,
  tree,
  initialMode = 'split',
  refresh,
  children,
}: {
  document: DocumentSummary;
  tree: DocumentTree | null;
  initialMode?: ViewMode;
  refresh: () => void;
  children: ReactNode;
}) {
  const [mode, setMode] = useState<ViewMode>(initialMode);
  const [pageCount, setPageCount] = useState<number | null>(
    document.page_count
  );
  const [currentPage, setCurrentPage] = useState(1);

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

  const scrollToPage = useCallback((page: number) => {
    setCurrentPage(page);
    for (const pane of ['original', 'extracted'] as const) {
      const element = anchors.current[pane].get(page);
      const container = element?.closest<HTMLElement>('[data-viewer-scroll]');

      // If a pane is hidden, continue
      if (!element?.offsetParent || !container) continue;

      container.scrollTop +=
        element.getBoundingClientRect().top -
        container.getBoundingClientRect().top -
        PAGE_SCROLL_MARGIN;
    }
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
      reportVisiblePage: setCurrentPage,
      refresh,
    }),
    [
      document,
      tree,
      pageCount,
      mode,
      currentPage,
      scrollToPage,
      registerPage,
      refresh,
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
  const { reportVisiblePage } = useViewer();
  return useCallback(
    (event: React.UIEvent<HTMLElement>) => {
      const container = event.currentTarget;
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
      if (page !== null) reportVisiblePage(page);
    },
    [pane, reportVisiblePage]
  );
}
