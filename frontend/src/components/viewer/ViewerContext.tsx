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

  // Null until the parsed content has loaded. Corrections made in the
  // session are applied here before a full reload
  tree: DocumentTree | null;

  // Whether the viewer may correct text or edit tables (owner or reviewer).
  canEdit: boolean;

  // Page count in the document, once known from either the file or the tree.
  pageCount: number | null;
  setPageCount: (count: number) => void;
  mode: ViewMode;
  setMode: (mode: ViewMode) => void;

  // The page the reader is on, from whichever pane they last scrolled.
  currentPage: number;

  // The page the viewer opened at from a ?page= link
  initialPage: number;

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

  // Pages the reader has switched back to the raw original in the original
  // pane. A rotated page (image_key set) shows its corrected upright image by
  // default; selecting its page adds it here so the un-rotated original is
  // shown instead, for comparison.
  originalPages: Set<number>;
  toggleOriginalPage: (page: number) => void;

  // The manual clockwise rotation (0/90/180/270) the reader has applied to
  // each page in the original pane, keyed by page number. View-only: it turns
  // the displayed page around in place so a scan the auto-correction missed
  // can be read, without touching the stored image or re-running OCR.
  pageRotations: Record<number, number>;
  rotatePage: (page: number) => void;

  // Track which region is currently active (hovered or clicked)
  activeRegionId: string | null;
  setActiveRegionId: (id: string | null) => void;

  showBoundingBoxes: boolean;
  setShowBoundingBoxes: (show: boolean) => void;

  showLowConfidence: boolean;
  setShowLowConfidence: (show: boolean) => void;

  regionFilter: string | null;
  setRegionFilter: (filter: string | null) => void;

  /**
   * Record a saved correction in the loaded tree: the active text becomes
   * `text` and the region is flagged with its preserved OCR output, until a
   * full refresh replaces the tree.
   */
  applyRegionCorrection: (regionId: string, text: string) => void;
  /** Record an undone correction in the loaded tree. */
  undoRegionCorrection: (regionId: string) => void;
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
  initialPage = 1,
  refresh,
  highlightRegionId: initialHighlightRegionId = null,
  highlightQuery: initialHighlightQuery = '',
  children,
}: {
  document: DocumentSummary;
  tree: DocumentTree | null;
  initialMode?: ViewMode;
  initialPage?: number;
  refresh: () => void;
  highlightRegionId?: string | null;
  highlightQuery?: string;
  children: ReactNode;
}) {
  const [mode, setMode] = useState<ViewMode>(initialMode);
  const [pageCount, setPageCount] = useState<number | null>(
    document.page_count
  );
  const [currentPage, setCurrentPage] = useState(initialPage);
  const [highlightRegionId, setHighlightRegionId] = useState<string | null>(
    initialHighlightRegionId
  );
  const [highlightQuery, setHighlightQuery] = useState(initialHighlightQuery);
  const [activeRegionId, setActiveRegionId] = useState<string | null>(null);
  const [showBoundingBoxes, setShowBoundingBoxes] = useState(true);
  const [showLowConfidence, setShowLowConfidence] = useState(false);
  const [regionFilter, setRegionFilter] = useState<string | null>(null);
  const [originalPages, setOriginalPages] = useState<Set<number>>(new Set());
  const [pageRotations, setPageRotations] = useState<Record<number, number>>(
    {}
  );

  const toggleOriginalPage = useCallback((page: number) => {
    setOriginalPages((current) => {
      const next = new Set(current);
      if (next.has(page)) next.delete(page);
      else next.add(page);
      return next;
    });
  }, []);

  const rotatePage = useCallback((page: number) => {
    setPageRotations((current) => ({
      ...current,
      [page]: ((current[page] ?? 0) + 90) % 360,
    }));
  }, []);

  // Corrections save through the API and then land here, so find-in-document
  // and the rendered text reflect them without waiting for a full reload.
  // The prop wins again whenever the page passes a new tree in (reset
  // during render, the React-endorsed way to adjust state to props).
  const [treeWithCorrections, setTreeWithCorrections] = useState(tree);
  const [lastLoadedTree, setLastLoadedTree] = useState(tree);
  if (tree !== lastLoadedTree) {
    setLastLoadedTree(tree);
    setTreeWithCorrections(tree);
  }

  const applyRegionCorrection = useCallback(
    (regionId: string, text: string) => {
      setTreeWithCorrections((current) => {
        if (!current) return current;
        const pages = current.pages.map((page) => ({
          ...page,
          regions: page.regions.map((region) =>
            region.id === regionId
              ? {
                  ...region,
                  text,
                  is_edited: true,
                  // The first correction freezes the OCR output as the
                  // diff's baseline; later corrections keep it.
                  original_text: region.is_edited
                    ? (region.original_text ?? null)
                    : (region.text ?? ''),
                }
              : region
          ),
        }));
        return { ...current, pages };
      });
    },
    []
  );

  const undoRegionCorrection = useCallback((regionId: string) => {
    setTreeWithCorrections((current) => {
      if (!current) return current;
      const pages = current.pages.map((page) => ({
        ...page,
        regions: page.regions.map((region) =>
          region.id === regionId
            ? {
                ...region,
                text: region.original_text ?? region.text,
                is_edited: false,
                original_text: null,
              }
            : region
        ),
      }));
      return { ...current, pages };
    });
  }, []);

  const setHighlight = useCallback((regionId: string | null, query: string) => {
    setHighlightRegionId(regionId);
    setHighlightQuery(query);
  }, []);

  // Read synchronously by scroll handlers, which can fire before a re-render
  const currentPageRef = useRef(initialPage);

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
      tree: treeWithCorrections,
      canEdit: treeWithCorrections?.can_edit ?? false,
      pageCount: pageCount ?? treeWithCorrections?.stats.page_count ?? null,
      setPageCount,
      mode,
      setMode,
      currentPage,
      initialPage,
      scrollToPage,
      registerPage,
      reportVisiblePage,
      highlightRegionId,
      highlightQuery,
      setHighlight,
      activeRegionId,
      setActiveRegionId,
      showBoundingBoxes,
      setShowBoundingBoxes,
      showLowConfidence,
      setShowLowConfidence,
      regionFilter,
      setRegionFilter,
      originalPages,
      toggleOriginalPage,
      pageRotations,
      rotatePage,
      applyRegionCorrection,
      undoRegionCorrection,
      refresh,
    }),
    [
      document,
      treeWithCorrections,
      pageCount,
      mode,
      currentPage,
      initialPage,
      scrollToPage,
      registerPage,
      refresh,
      reportVisiblePage,
      highlightRegionId,
      highlightQuery,
      setHighlight,
      activeRegionId,
      showBoundingBoxes,
      showLowConfidence,
      regionFilter,
      originalPages,
      toggleOriginalPage,
      pageRotations,
      rotatePage,
      applyRegionCorrection,
      undoRegionCorrection,
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
