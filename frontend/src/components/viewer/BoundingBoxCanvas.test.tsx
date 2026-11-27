import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import type { DocumentSummary } from '@/lib/documents';
import type { DocumentTree, PageNode, RegionNode } from '@/lib/viewer';
import { BoundingBoxCanvas } from './BoundingBoxCanvas';
import { ViewerProvider, useViewer } from './ViewerContext';
import { ViewerHeader } from './ViewerHeader';
import { PageFrame } from './PageView';

afterEach(cleanup);

function makeRegion(
  id: string,
  bbox: { x0: number; y0: number; x1: number; y1: number },
  confidence: number = 0.95
): RegionNode {
  return {
    id,
    page_number: 1,
    reading_order: 0,
    region_type: 'paragraph',
    bbox,
    confidence,
    text: `Text of ${id}`,
    text_confidence: confidence,
    table_id: null,
    region_table_id: null,
    is_first_table_part: false,
    image_key: null,
    caption: null,
  };
}

function makePage(
  page_number: number = 1,
  width: number = 1000,
  height: number = 2000,
  regions: RegionNode[] = []
): PageNode {
  return {
    page_number,
    width,
    height,
    status: 'completed',
    error_message: null,
    image_key: null,
    has_thumbnail: false,
    regions,
    warnings: [],
  };
}

const DOCUMENT: DocumentSummary = {
  id: 'doc-zoom-test',
  filename: 'zoom-spec.pdf',
  page_count: 1,
  status: 'completed',
} as DocumentSummary;

function makeTree(pageNode: PageNode): DocumentTree {
  return {
    schema_version: 1,
    generated_at: '2026-11-27T00:00:00Z',
    document: DOCUMENT,
    pages_included: [pageNode.page_number],
    can_edit: true,
    pages: [pageNode],
    tables: [],
    stats: {
      page_count: 1,
      high_confidence_page_count: 1,
      region_count: pageNode.regions.length,
      table_count: 0,
      stitched_table_count: 0,
      warning_count: 0,
    },
  } as DocumentTree;
}

function renderCanvas(
  pageNode: PageNode,
  displayWidth: number,
  displayHeight: number,
  initialZoom: number = 1.0
) {
  const tree = makeTree(pageNode);
  return render(
    <ViewerProvider
      document={DOCUMENT}
      tree={tree}
      refresh={() => {}}
      initialZoom={initialZoom}
    >
      <BoundingBoxCanvas
        page={pageNode}
        pageNumber={pageNode.page_number}
        displayWidth={displayWidth}
        displayHeight={displayHeight}
      />
    </ViewerProvider>
  );
}

function parseBoxPixels(element: HTMLElement) {
  const left = parseInt(element.style.left, 10);
  const top = parseInt(element.style.top, 10);
  const width = parseInt(element.style.width, 10);
  const height = parseInt(element.style.height, 10);
  return {
    left,
    top,
    width,
    height,
    corners: {
      topLeft: { x: left, y: top },
      topRight: { x: left + width, y: top },
      bottomLeft: { x: left, y: top + height },
      bottomRight: { x: left + width, y: top + height },
    },
  };
}

describe('BoundingBoxCanvas zoom alignment', () => {
  it('renders bounding boxes with correct 4 corners at 100% zoom', () => {
    // Page: 1000 x 2000
    // Display: 500 x 1000 (scaleX = 0.5, scaleY = 0.5)
    // Bbox: (100, 200, 500, 800)
    const region = makeRegion('r1', { x0: 100, y0: 200, x1: 500, y1: 800 });
    const page = makePage(1, 1000, 2000, [region]);

    renderCanvas(page, 500, 1000);

    const box = document.querySelector(
      '[data-region-id="r1"]'
    ) as HTMLElement | null;
    expect(box).not.toBeNull();

    const { left, top, width, height, corners } = parseBoxPixels(box!);
    expect(left).toBe(50);
    expect(top).toBe(100);
    expect(width).toBe(200);
    expect(height).toBe(300);

    // Four corners check
    expect(corners.topLeft).toEqual({ x: 50, y: 100 });
    expect(corners.topRight).toEqual({ x: 250, y: 100 });
    expect(corners.bottomLeft).toEqual({ x: 50, y: 400 });
    expect(corners.bottomRight).toEqual({ x: 250, y: 400 });
  });

  it('enlarges bounding boxes when zooming in and four corners remain in place relative to document', () => {
    // Page: 1000 x 2000
    // 100% zoom display: 500 x 1000 (scale: 0.5)
    // 150% zoom display: 750 x 1500 (scale: 0.75)
    // 200% zoom display: 1000 x 2000 (scale: 1.0)
    const region = makeRegion('r1', { x0: 200, y0: 400, x1: 600, y1: 1200 });
    const page = makePage(1, 1000, 2000, [region]);

    // Zoom 100%: display 500 x 1000
    const { unmount } = renderCanvas(page, 500, 1000, 1.0);
    const box100 = parseBoxPixels(
      document.querySelector('[data-region-id="r1"]') as HTMLElement
    );
    expect(box100.left).toBe(100);
    expect(box100.top).toBe(200);
    expect(box100.width).toBe(200);
    expect(box100.height).toBe(400);
    expect(box100.corners.topLeft).toEqual({ x: 100, y: 200 });
    expect(box100.corners.topRight).toEqual({ x: 300, y: 200 });
    expect(box100.corners.bottomLeft).toEqual({ x: 100, y: 600 });
    expect(box100.corners.bottomRight).toEqual({ x: 300, y: 600 });
    unmount();

    // Zoom 150% (enlarged): display 750 x 1500
    const { unmount: unmount150 } = renderCanvas(page, 750, 1500, 1.5);
    const box150 = parseBoxPixels(
      document.querySelector('[data-region-id="r1"]') as HTMLElement
    );
    expect(box150.left).toBe(150);
    expect(box150.top).toBe(300);
    expect(box150.width).toBe(300);
    expect(box150.height).toBe(600);
    expect(box150.width).toBeGreaterThan(box100.width);
    expect(box150.height).toBeGreaterThan(box100.height);
    // Four corners scale exactly 1.5x relative to the 100% position
    expect(box150.corners.topLeft).toEqual({ x: 150, y: 300 });
    expect(box150.corners.topRight).toEqual({ x: 450, y: 300 });
    expect(box150.corners.bottomLeft).toEqual({ x: 150, y: 900 });
    expect(box150.corners.bottomRight).toEqual({ x: 450, y: 900 });
    unmount150();

    // Zoom 200% (enlarged further): display 1000 x 2000
    renderCanvas(page, 1000, 2000, 2.0);
    const box200 = parseBoxPixels(
      document.querySelector('[data-region-id="r1"]') as HTMLElement
    );
    expect(box200.left).toBe(200);
    expect(box200.top).toBe(400);
    expect(box200.width).toBe(400);
    expect(box200.height).toBe(800);
    expect(box200.corners.topLeft).toEqual({ x: 200, y: 400 });
    expect(box200.corners.topRight).toEqual({ x: 600, y: 400 });
    expect(box200.corners.bottomLeft).toEqual({ x: 200, y: 1200 });
    expect(box200.corners.bottomRight).toEqual({ x: 600, y: 1200 });
  });

  it('shrinks bounding boxes when zooming out and four corners remain in place relative to document', () => {
    // Page: 1000 x 2000
    // 100% zoom: display 800 x 1600 (scale: 0.8)
    // 50% zoom: display 400 x 800 (scale: 0.4)
    const region = makeRegion('r2', { x0: 100, y0: 200, x1: 500, y1: 600 });
    const page = makePage(1, 1000, 2000, [region]);

    const { unmount } = renderCanvas(page, 800, 1600, 1.0);
    const box100 = parseBoxPixels(
      document.querySelector('[data-region-id="r2"]') as HTMLElement
    );
    expect(box100.left).toBe(80);
    expect(box100.top).toBe(160);
    expect(box100.width).toBe(320);
    expect(box100.height).toBe(320);
    unmount();

    // Zoom out to 50%
    renderCanvas(page, 400, 800, 0.5);
    const box50 = parseBoxPixels(
      document.querySelector('[data-region-id="r2"]') as HTMLElement
    );
    expect(box50.left).toBe(40);
    expect(box50.top).toBe(80);
    expect(box50.width).toBe(160);
    expect(box50.height).toBe(160);
    expect(box50.width).toBeLessThan(box100.width);
    expect(box50.height).toBeLessThan(box100.height);
    // Four corners check at 50%
    expect(box50.corners.topLeft).toEqual({ x: 40, y: 80 });
    expect(box50.corners.topRight).toEqual({ x: 200, y: 80 });
    expect(box50.corners.bottomLeft).toEqual({ x: 40, y: 240 });
    expect(box50.corners.bottomRight).toEqual({ x: 200, y: 240 });
  });

  it('updates bounding box positions when window size or orientation changes while zoomed in', () => {
    // Zoomed in at 150%: initial window width gives display width 900 x 1800
    const region = makeRegion('r3', { x0: 200, y0: 300, x1: 600, y1: 700 });
    const page = makePage(1, 1000, 2000, [region]);
    const tree = makeTree(page);

    function ResizableViewer({
      width,
      height,
    }: {
      width: number;
      height: number;
    }) {
      return (
        <ViewerProvider
          document={DOCUMENT}
          tree={tree}
          refresh={() => {}}
          initialZoom={1.5}
        >
          <BoundingBoxCanvas
            page={page}
            pageNumber={1}
            displayWidth={width}
            displayHeight={height}
          />
        </ViewerProvider>
      );
    }

    const { rerender } = render(<ResizableViewer width={900} height={1800} />);
    const initialBox = parseBoxPixels(
      document.querySelector('[data-region-id="r3"]') as HTMLElement
    );
    expect(initialBox.left).toBe(180);
    expect(initialBox.top).toBe(270);
    expect(initialBox.width).toBe(360);
    expect(initialBox.height).toBe(360);
    expect(initialBox.corners.topLeft).toEqual({ x: 180, y: 270 });
    expect(initialBox.corners.bottomRight).toEqual({ x: 540, y: 630 });

    // Window resized / layout changed while zoomed in:
    // e.g. sidebar collapsed or orientation changed to landscape: width increases to 1200 x 2400
    rerender(<ResizableViewer width={1200} height={2400} />);
    const resizedBox = parseBoxPixels(
      document.querySelector('[data-region-id="r3"]') as HTMLElement
    );
    expect(resizedBox.left).toBe(240);
    expect(resizedBox.top).toBe(360);
    expect(resizedBox.width).toBe(480);
    expect(resizedBox.height).toBe(480);
    expect(resizedBox.corners.topLeft).toEqual({ x: 240, y: 360 });
    expect(resizedBox.corners.bottomRight).toEqual({ x: 720, y: 840 });

    // Orientation changed to portrait/narrow: width decreases to 600 x 1200
    rerender(<ResizableViewer width={600} height={1200} />);
    const narrowBox = parseBoxPixels(
      document.querySelector('[data-region-id="r3"]') as HTMLElement
    );
    expect(narrowBox.left).toBe(120);
    expect(narrowBox.top).toBe(180);
    expect(narrowBox.width).toBe(240);
    expect(narrowBox.height).toBe(240);
    expect(narrowBox.corners.topLeft).toEqual({ x: 120, y: 180 });
    expect(narrowBox.corners.bottomRight).toEqual({ x: 360, y: 420 });
  });

  it('clamps minimum width and height to 2px for zero-dimension regions', () => {
    const region = makeRegion('r-tiny', { x0: 50, y0: 50, x1: 50, y1: 50 });
    const page = makePage(1, 1000, 1000, [region]);

    renderCanvas(page, 500, 500);

    const box = parseBoxPixels(
      document.querySelector('[data-region-id="r-tiny"]') as HTMLElement
    );
    expect(box.left).toBe(25);
    expect(box.top).toBe(25);
    expect(box.width).toBe(2);
    expect(box.height).toBe(2);
  });

  it('renders nothing when display dimensions or page dimensions are non-positive', () => {
    const region = makeRegion('r-none', { x0: 10, y0: 10, x1: 50, y1: 50 });
    const page = makePage(1, 1000, 1000, [region]);

    const { unmount } = renderCanvas(page, 0, 0);
    expect(document.querySelector('[data-region-id="r-none"]')).toBeNull();
    unmount();

    renderCanvas(page, -100, -100);
    expect(document.querySelector('[data-region-id="r-none"]')).toBeNull();
  });

  it('handles active region selection on click and hover', () => {
    const region = makeRegion(
      'r-active',
      { x0: 100, y0: 100, x1: 300, y1: 300 },
      0.88
    );
    const page = makePage(1, 1000, 1000, [region]);

    renderCanvas(page, 500, 500);
    const box = document.querySelector(
      '[data-region-id="r-active"]'
    ) as HTMLElement;

    expect(box.className).toContain('border-transparent');

    fireEvent.click(box);
    expect(box.className).toContain('border-blue-500');

    // Clicking again deselects
    fireEvent.click(box);
    expect(box.className).toContain('border-transparent');
  });
});

describe('Zoom controls in ViewerHeader', () => {
  function HeaderWithContext({ initialZoom = 1.0 }: { initialZoom?: number }) {
    const page = makePage();
    const tree = makeTree(page);
    return (
      <ViewerProvider
        document={DOCUMENT}
        tree={tree}
        refresh={() => {}}
        initialZoom={initialZoom}
      >
        <ViewerHeader />
      </ViewerProvider>
    );
  }

  it('displays initial zoom percentage', () => {
    render(<HeaderWithContext initialZoom={1.0} />);
    expect(
      screen.getByRole('button', { name: /Reset zoom, currently 100%/i })
    ).toBeDefined();
    expect(screen.getByText('100%')).toBeDefined();
  });

  it('zooms in when clicking Zoom in button and updates display', () => {
    render(<HeaderWithContext initialZoom={1.0} />);
    const zoomInBtn = screen.getByRole('button', { name: 'Zoom in' });

    fireEvent.click(zoomInBtn);
    expect(screen.getByText('125%')).toBeDefined();

    fireEvent.click(zoomInBtn);
    expect(screen.getByText('150%')).toBeDefined();
  });

  it('zooms out when clicking Zoom out button and updates display', () => {
    render(<HeaderWithContext initialZoom={1.0} />);
    const zoomOutBtn = screen.getByRole('button', { name: 'Zoom out' });

    fireEvent.click(zoomOutBtn);
    expect(screen.getByText('75%')).toBeDefined();

    fireEvent.click(zoomOutBtn);
    expect(screen.getByText('50%')).toBeDefined();
  });

  it('resets zoom to 100% when clicking the percentage button', () => {
    render(<HeaderWithContext initialZoom={1.75} />);
    const resetBtn = screen.getByRole('button', {
      name: /Reset zoom, currently 175%/i,
    });
    expect(screen.getByText('175%')).toBeDefined();

    fireEvent.click(resetBtn);
    expect(screen.getByText('100%')).toBeDefined();
  });

  it('disables zoom out at minimum zoom (50%) and zoom in at maximum zoom (300%)', () => {
    const { unmount } = render(<HeaderWithContext initialZoom={0.5} />);
    const zoomOutBtn = screen.getByRole('button', { name: 'Zoom out' });
    expect(zoomOutBtn.hasAttribute('disabled')).toBe(true);
    unmount();

    render(<HeaderWithContext initialZoom={3.0} />);
    const zoomInBtn = screen.getByRole('button', { name: 'Zoom in' });
    expect(zoomInBtn.hasAttribute('disabled')).toBe(true);
  });
});

describe('PageFrame and ResizeObserver layout scaling', () => {
  it('updates overlay display dimensions when frame element resizes', () => {
    let resizeCallback: ResizeObserverCallback | null = null;
    class MockResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = callback;
      }
      observe() {}
      disconnect() {}
      unobserve() {}
    }
    vi.stubGlobal('ResizeObserver', MockResizeObserver);

    const region = makeRegion('r-frame', {
      x0: 100,
      y0: 200,
      x1: 500,
      y1: 800,
    });
    const page = makePage(1, 1000, 2000, [region]);
    const tree = makeTree(page);

    render(
      <ViewerProvider document={DOCUMENT} tree={tree} refresh={() => {}}>
        <PageFrame
          pageNumber={1}
          aspectRatio={0.5}
          renderOverlay={(info) => <BoundingBoxCanvas {...info} />}
        >
          <div data-testid="page-content" />
        </PageFrame>
      </ViewerProvider>
    );

    // Initially size is 0, so overlay is not rendered
    expect(document.querySelector('[data-region-id="r-frame"]')).toBeNull();

    // Trigger ResizeObserver with 100% zoom (e.g. 500 x 1000)
    expect(resizeCallback).not.toBeNull();
    act(() => {
      resizeCallback!(
        [
          {
            contentRect: { width: 500, height: 1000 },
          } as ResizeObserverEntry,
        ],
        {} as ResizeObserver
      );
    });

    const box1 = parseBoxPixels(
      document.querySelector('[data-region-id="r-frame"]') as HTMLElement
    );
    expect(box1.left).toBe(50);
    expect(box1.top).toBe(100);
    expect(box1.width).toBe(200);
    expect(box1.height).toBe(300);

    // Zoom in triggers ResizeObserver with 200% zoom (1000 x 2000)
    act(() => {
      resizeCallback!(
        [
          {
            contentRect: { width: 1000, height: 2000 },
          } as ResizeObserverEntry,
        ],
        {} as ResizeObserver
      );
    });

    const box2 = parseBoxPixels(
      document.querySelector('[data-region-id="r-frame"]') as HTMLElement
    );
    expect(box2.left).toBe(100);
    expect(box2.top).toBe(200);
    expect(box2.width).toBe(400);
    expect(box2.height).toBe(600);
    expect(box2.corners.topLeft).toEqual({ x: 100, y: 200 });
    expect(box2.corners.bottomRight).toEqual({ x: 500, y: 800 });

    vi.unstubAllGlobals();
  });
});
