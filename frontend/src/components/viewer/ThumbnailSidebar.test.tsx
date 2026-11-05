import { afterEach, beforeAll, beforeEach, expect, test, vi } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import api from '@/lib/api';
import type { DocumentSummary } from '@/lib/documents';
import type { DocumentTree, PageNode } from '@/lib/viewer';
import { THUMBNAIL_SIDEBAR_ID, ThumbnailSidebar } from './ThumbnailSidebar';
import {
  THUMBNAILS_OPEN_STORAGE_KEY,
  ViewerProvider,
  usePageTracking,
  useViewer,
} from './ViewerContext';
import { useViewerShortcuts } from './useViewerShortcuts';

vi.mock('@/lib/api', () => ({ default: { get: vi.fn() } }));

const PAGE_HEIGHT = 1000;
const scrollIntoView = vi.fn();
let wideScreen = true;

beforeAll(() => {
  const scrollTops = new WeakMap<Element, number>();
  Object.defineProperty(HTMLElement.prototype, 'scrollTop', {
    configurable: true,
    get() {
      return scrollTops.get(this) ?? 0;
    },
    set(value: number) {
      scrollTops.set(this, Math.max(0, value));
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get() {
      return 600;
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetParent', {
    configurable: true,
    get() {
      return this.closest('[hidden]') ? null : this.parentElement;
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
  HTMLElement.prototype.scrollIntoView = scrollIntoView;
  window.matchMedia = ((query: string) => ({
    matches: wideScreen,
    media: query,
  })) as unknown as typeof window.matchMedia;
  let next = 0;
  URL.createObjectURL = () => `blob:thumbnail-${(next += 1)}`;
  URL.revokeObjectURL = () => {};
});

beforeEach(() => {
  wideScreen = true;
  localStorage.clear();
  scrollIntoView.mockClear();
  vi.mocked(api.get).mockReset();
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.endsWith('/pages/3/thumbnail')) throw new Error('404');
    return { data: new Blob(['webp']) };
  });
});

afterEach(cleanup);

function page(page_number: number, has_thumbnail: boolean): PageNode {
  return {
    page_number,
    width: 850,
    height: 1100,
    status: 'completed',
    error_message: null,
    image_key: null,
    has_thumbnail,
    regions: [],
    warnings: [],
  };
}

const DOCUMENT = {
  id: 'doc-1',
  filename: 'report.pdf',
  page_count: 4,
  status: 'completed',
} as DocumentSummary;

const TREE = {
  generated_at: '2026-11-04T12:00:00Z',
  document: DOCUMENT,
  pages_included: [1, 2, 3, 4],
  pages: [page(1, true), page(2, true), page(3, true), page(4, false)],
  can_edit: false,
  tables: [],
  stats: { page_count: 4 },
} as unknown as DocumentTree;

function Pane() {
  const onScroll = usePageTracking('original');
  const { registerPage, currentPage, pageCount } = useViewer();
  return (
    <>
      <p data-testid="indicator">
        Page {currentPage} of {pageCount}
      </p>
      <div data-viewer-scroll data-testid="pane" onScroll={onScroll}>
        {[1, 2, 3, 4].map((number) => (
          <div
            key={number}
            data-viewer-page={number}
            data-pane="original"
            ref={(element) => registerPage('original', number, element)}
          />
        ))}
      </div>
    </>
  );
}

function Shortcuts() {
  useViewerShortcuts();
  return null;
}

function renderViewer() {
  return render(
    <ViewerProvider document={DOCUMENT} tree={TREE} refresh={() => {}}>
      <Shortcuts />
      <ThumbnailSidebar />
      <Pane />
    </ViewerProvider>
  );
}

function sidebar() {
  const nav = document.getElementById(THUMBNAIL_SIDEBAR_ID)!;
  expect(nav.tagName).toBe('NAV');
  expect(nav.getAttribute('aria-label')).toBe('Page thumbnails');
  return nav;
}

function thumbnail(number: number) {
  return within(sidebar()).getByRole('button', {
    name: `Page ${number}`,
    hidden: true,
  });
}

test('the sidebar renders a numbered list of page thumbnails', async () => {
  renderViewer();

  const items = within(sidebar()).getAllByRole('listitem');
  expect(items).toHaveLength(4);
  expect(
    items.map(
      (item) => item.querySelector('button')?.lastElementChild?.textContent
    )
  ).toEqual(['1', '2', '3', '4']);

  await waitFor(() =>
    expect(thumbnail(1).querySelector('img')?.getAttribute('src')).toMatch(
      /^blob:/
    )
  );
  expect(thumbnail(2).querySelector('img')).not.toBeNull();
  expect(api.get).toHaveBeenCalledWith(
    '/api/v1/documents/doc-1/pages/1/thumbnail',
    { responseType: 'blob' }
  );
  await waitFor(() =>
    expect(
      within(thumbnail(3)).getByTestId('thumbnail-placeholder')
    ).toBeTruthy()
  );
  expect(
    within(thumbnail(4)).getByTestId('thumbnail-placeholder')
  ).toBeTruthy();
  expect(api.get).not.toHaveBeenCalledWith(
    '/api/v1/documents/doc-1/pages/4/thumbnail',
    expect.anything()
  );
});

test('clicking a thumbnail scrolls to its page and marks it active', () => {
  renderViewer();
  const pane = screen.getByTestId('pane');

  fireEvent.click(thumbnail(3));

  expect(pane.scrollTop).toBe(2 * PAGE_HEIGHT - 16);
  expect(screen.getByTestId('indicator').textContent).toBe('Page 3 of 4');
  expect(thumbnail(3).getAttribute('aria-current')).toBe('page');
  expect(thumbnail(3).className).toContain('ring-slate-900');
  expect(thumbnail(1).getAttribute('aria-current')).toBeNull();
});

test('the active thumbnail follows scrolling and is kept in view', () => {
  renderViewer();
  const pane = screen.getByTestId('pane');
  scrollIntoView.mockClear();

  pane.scrollTop = PAGE_HEIGHT;
  fireEvent.scroll(pane);

  expect(thumbnail(2).getAttribute('aria-current')).toBe('page');
  expect(thumbnail(1).getAttribute('aria-current')).toBeNull();
  expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
  expect(scrollIntoView.mock.contexts.at(-1)).toBe(thumbnail(2));
});

test('the collapsed state persists across visits', () => {
  renderViewer();
  expect(sidebar().hidden).toBe(false);

  act(() => {
    fireEvent.keyDown(window, { key: 't' });
  });

  expect(sidebar().hidden).toBe(true);
  expect(localStorage.getItem(THUMBNAILS_OPEN_STORAGE_KEY)).toBe('false');

  cleanup();
  renderViewer();
  expect(sidebar().hidden).toBe(true);
});

test('the sidebar starts closed on narrow screens until opened', () => {
  wideScreen = false;
  renderViewer();
  expect(sidebar().hidden).toBe(true);

  act(() => {
    fireEvent.keyDown(window, { key: 'T' });
  });

  expect(sidebar().hidden).toBe(false);
  expect(localStorage.getItem(THUMBNAILS_OPEN_STORAGE_KEY)).toBe('true');
});

test('one T press toggles the sidebar exactly once', () => {
  renderViewer();
  expect(sidebar().hidden).toBe(false);

  act(() => {
    fireEvent.keyDown(document.body, { key: 't' });
  });

  expect(sidebar().hidden).toBe(true);
  expect(localStorage.getItem(THUMBNAILS_OPEN_STORAGE_KEY)).toBe('false');
});

test('typing in a field does not toggle the sidebar', () => {
  renderViewer();
  const input = document.createElement('input');
  document.body.appendChild(input);

  fireEvent.keyDown(input, { key: 't' });

  expect(sidebar().hidden).toBe(false);
  input.remove();
});
