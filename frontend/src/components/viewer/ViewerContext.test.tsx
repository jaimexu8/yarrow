import { afterEach, beforeAll, expect, test } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { DocumentSummary } from '@/lib/documents';
import {
  ViewerProvider,
  usePageTracking,
  useViewer,
  type Pane,
} from './ViewerContext';

const PAGE_HEIGHT = 1000;
// The reading line sits a third of the way down: 200px below the pane's top
const PANE_HEIGHT = 600;
// Matches PAGE_SCROLL_MARGIN in ViewerContext
const PAGE_SCROLL_MARGIN = 16;

beforeAll(() => {
  // jsdom has no layout, so fake one: every pane sits at y=0 and its pages
  // are stacked PAGE_HEIGHT apart, moving up as the pane scrolls
  const scrollTops = new WeakMap<Element, number>();
  Object.defineProperty(HTMLElement.prototype, 'scrollTop', {
    configurable: true,
    get() {
      return scrollTops.get(this) ?? 0;
    },
    set(value: number) {
      // Browsers clamp scrolling at the top of the content
      scrollTops.set(this, Math.max(0, value));
    },
  });

  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get() {
      return PANE_HEIGHT;
    },
  });

  // Every pane counts as visible
  Object.defineProperty(HTMLElement.prototype, 'offsetParent', {
    configurable: true,
    get() {
      return this.parentElement;
    },
  });

  HTMLElement.prototype.getBoundingClientRect = function () {
    const page = this.dataset.viewerPage;
    if (page === undefined) return { top: 0 } as DOMRect;
    const container = this.closest<HTMLElement>('[data-viewer-scroll]')!;
    return {
      top: (Number(page) - 1) * PAGE_HEIGHT - container.scrollTop,
    } as DOMRect;
  };
});

afterEach(cleanup);

function TestPane({ pane }: { pane: Pane }) {
  const onScroll = usePageTracking(pane);
  const { registerPage } = useViewer();
  return (
    <div data-viewer-scroll data-testid={pane} onScroll={onScroll}>
      {[1, 2, 3].map((page) => (
        <div
          key={page}
          data-viewer-page={page}
          data-pane={pane}
          ref={(element) => registerPage(pane, page, element)}
        />
      ))}
    </div>
  );
}

function renderViewer() {
  render(
    <ViewerProvider document={{ page_count: 3 } as DocumentSummary} tree={null}>
      <TestPane pane="original" />
      <TestPane pane="extracted" />
    </ViewerProvider>
  );
  return {
    original: screen.getByTestId('original'),
    extracted: screen.getByTestId('extracted'),
  };
}

function scrollTo(element: HTMLElement, top: number) {
  element.scrollTop = top;
  fireEvent.scroll(element);
}

test('scrolling one pane to a new page brings the other pane to that page', () => {
  const { original, extracted } = renderViewer();

  // Page 2 now crosses the reading line of the original pane
  scrollTo(original, PAGE_HEIGHT);

  expect(extracted.scrollTop).toBe(PAGE_HEIGHT - PAGE_SCROLL_MARGIN);
});

test('the synced pane does not scroll the original pane back', () => {
  const { original, extracted } = renderViewer();
  scrollTo(original, PAGE_HEIGHT);

  // The scroll event caused by the sync itself
  fireEvent.scroll(extracted);

  expect(original.scrollTop).toBe(PAGE_HEIGHT);
});

test('scrolling within the same page leaves the other pane alone', () => {
  const { original, extracted } = renderViewer();
  scrollTo(original, PAGE_HEIGHT);

  // Still on page 2
  scrollTo(original, PAGE_HEIGHT + 100);

  expect(extracted.scrollTop).toBe(PAGE_HEIGHT - PAGE_SCROLL_MARGIN);
});

test('scrolling back to an earlier page syncs the other pane too', () => {
  const { original, extracted } = renderViewer();

  scrollTo(extracted, 2 * PAGE_HEIGHT);
  scrollTo(extracted, 0);

  expect(original.scrollTop).toBe(0);
});
