import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import type { DocumentSummary } from '@/lib/documents';
import type { DocumentTree, PageNode, RegionNode } from '@/lib/viewer';
import { VIEWER_SHORTCUTS } from '@/lib/viewerShortcuts';
import type { ReactNode } from 'react';
import { InlineRegionEditor } from './InlineRegionEditor';
import { KeyboardShortcutsModal } from './KeyboardShortcutsModal';
import { ViewerProvider, useViewer } from './ViewerContext';
import { useViewerShortcuts } from './useViewerShortcuts';

vi.mock('@/lib/corrections', () => ({
  updateRegionText: vi.fn(),
  correctionErrorDetail: () => null,
}));

const scrollIntoView = vi.fn();

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (
    this: HTMLDialogElement
  ) {
    this.open = true;
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.open = false;
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetParent', {
    configurable: true,
    get() {
      return this.closest('[hidden]') ? null : this.parentElement;
    },
  });
  HTMLElement.prototype.scrollIntoView = scrollIntoView;
});

afterEach(() => {
  cleanup();
  scrollIntoView.mockClear();
  localStorage.clear();
});

function region(
  id: string,
  page_number: number,
  reading_order: number,
  confidence: number
): RegionNode {
  return {
    id,
    page_number,
    reading_order,
    region_type: 'text',
    bbox: { x0: 0, y0: 0, x1: 10, y1: 10 },
    confidence,
    text: id,
    text_confidence: confidence,
    table_id: null,
    region_table_id: null,
    is_first_table_part: false,
    image_key: null,
    caption: null,
  };
}

function page(page_number: number, regions: RegionNode[]): PageNode {
  return {
    page_number,
    width: 850,
    height: 1100,
    status: 'completed',
    error_message: null,
    image_key: null,
    has_thumbnail: false,
    regions,
    warnings: [],
  };
}

const DOCUMENT = {
  id: 'doc-1',
  filename: 'report.pdf',
  page_count: 4,
  status: 'completed',
} as DocumentSummary;

const FLAGGED_PAGES = [
  page(1, [region('p1-clear', 1, 0, 0.9), region('p1-low', 1, 1, 0.3)]),
  page(2, [region('p2-second', 2, 1, 0.4), region('p2-first', 2, 0, 0.2)]),
  page(3, [region('p3-clear', 3, 0, 0.8)]),
  page(4, [region('p4-low', 4, 2, 0.1)]),
];

function treeWith(pages: PageNode[]): DocumentTree {
  return {
    schema_version: 1,
    generated_at: '2026-11-05T00:00:00Z',
    document: DOCUMENT,
    pages_included: pages.map((item) => item.page_number),
    can_edit: true,
    pages,
    tables: [],
    stats: {
      page_count: pages.length,
      high_confidence_page_count: 0,
      region_count: 0,
      table_count: 0,
      stitched_table_count: 0,
      warning_count: 0,
    },
  } as DocumentTree;
}

function Layout() {
  const viewer = useViewer();
  const announcement = useViewerShortcuts();
  return (
    <>
      <p aria-live="polite" data-testid="announcement">
        {announcement}
      </p>
      <dl data-testid="state">
        <dt>page</dt>
        <dd data-testid="page">{viewer.currentPage}</dd>
        <dt>active</dt>
        <dd data-testid="active">{viewer.activeRegionId ?? 'none'}</dd>
        <dt>boxes</dt>
        <dd data-testid="boxes">{String(viewer.showBoundingBoxes)}</dd>
        <dt>thumbnails</dt>
        <dd data-testid="thumbnails">{String(viewer.thumbnailsOpen)}</dd>
        <dt>low</dt>
        <dd data-testid="low">{String(viewer.showLowConfidence)}</dd>
      </dl>
      <button
        type="button"
        onClick={() => viewer.setActiveRegionId('p1-clear')}
      >
        Activate clear region
      </button>
      <div data-testid="regions">
        {viewer.tree?.pages
          .flatMap((item) => item.regions)
          .map((item) => (
            <div key={item.id} data-region-id={item.id} />
          ))}
      </div>
      <KeyboardShortcutsModal
        open={viewer.shortcutsHelpOpen}
        onClose={() => viewer.setShortcutsHelpOpen(false)}
      />
    </>
  );
}

function renderViewer(pages: PageNode[] = FLAGGED_PAGES, children?: ReactNode) {
  return render(
    <ViewerProvider
      document={DOCUMENT}
      tree={treeWith(pages)}
      refresh={() => {}}
    >
      <Layout />
      {children}
    </ViewerProvider>
  );
}

function press(key: string, init: KeyboardEventInit = {}) {
  let event!: KeyboardEvent;
  act(() => {
    event = new KeyboardEvent('keydown', {
      key,
      bubbles: true,
      cancelable: true,
      ...init,
    });
    (window.document.activeElement ?? window.document.body).dispatchEvent(
      event
    );
  });
  return event;
}

function text(testId: string) {
  return screen.getByTestId(testId).textContent;
}

describe('page navigation', () => {
  it('moves forward and back with the arrow keys and J/K', () => {
    renderViewer();

    expect(press('j').defaultPrevented).toBe(true);
    expect(text('page')).toBe('2');
    press('ArrowRight');
    expect(text('page')).toBe('3');
    press('k');
    expect(text('page')).toBe('2');
    press('ArrowLeft');
    expect(text('page')).toBe('1');
    press('J');
    expect(text('page')).toBe('2');
  });

  it('stays put at the first and last page', () => {
    renderViewer();

    press('k');
    expect(text('page')).toBe('1');
    for (let index = 0; index < 6; index += 1) press('j');
    expect(text('page')).toBe('4');
    press('ArrowRight');
    expect(text('page')).toBe('4');
  });

  it('leaves modified keys to the browser', () => {
    renderViewer();

    expect(press('ArrowRight', { altKey: true }).defaultPrevented).toBe(false);
    expect(press('j', { ctrlKey: true }).defaultPrevented).toBe(false);
    expect(text('page')).toBe('1');
  });
});

describe('low-confidence navigation', () => {
  it('steps through flagged regions by page and reading order, wrapping around', () => {
    renderViewer();

    const visited: string[] = [];
    for (let index = 0; index < 5; index += 1) {
      press('n');
      visited.push(text('active')!);
    }

    expect(visited).toEqual([
      'p1-low',
      'p2-first',
      'p2-second',
      'p4-low',
      'p1-low',
    ]);
    expect(text('low')).toBe('true');
    expect(text('page')).toBe('1');
    expect(text('announcement')).toBe('Low-confidence region 1 of 4, page 1');
  });

  it('steps backwards with Shift+N and wraps to the last region', () => {
    renderViewer();

    press('N', { shiftKey: true });
    expect(text('active')).toBe('p4-low');
    expect(text('page')).toBe('4');
    expect(text('announcement')).toBe('Low-confidence region 4 of 4, page 4');

    press('N', { shiftKey: true });
    expect(text('active')).toBe('p2-second');
    press('N', { shiftKey: true });
    expect(text('active')).toBe('p2-first');
  });

  it('starts from the current page when no region is active', () => {
    renderViewer();
    press('j');
    press('j');

    press('n');
    expect(text('active')).toBe('p4-low');

    fireEvent.click(screen.getByText('Activate clear region'));
    press('N', { shiftKey: true });
    expect(text('active')).toBe('p4-low');
  });

  it('continues after the active region even when it is not flagged', () => {
    renderViewer();
    press('j');
    press('j');
    fireEvent.click(screen.getByText('Activate clear region'));

    press('n');
    expect(text('active')).toBe('p1-low');
    expect(text('page')).toBe('1');
  });

  it('scrolls the region into view in every visible pane', () => {
    renderViewer();
    const hiddenPane = document.createElement('div');
    hiddenPane.hidden = true;
    const hiddenCopy = document.createElement('div');
    hiddenCopy.dataset.regionId = 'p1-low';
    hiddenPane.appendChild(hiddenCopy);
    document.body.appendChild(hiddenPane);

    press('n');

    const visible = screen
      .getByTestId('regions')
      .querySelector('[data-region-id="p1-low"]');
    expect(visible).not.toBeNull();
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center' });
    expect(scrollIntoView.mock.contexts[0]).toBe(visible);
    hiddenPane.remove();
  });

  it('announces when the document has no low-confidence regions', () => {
    renderViewer([page(1, [region('clear', 1, 0, 0.95)])]);

    press('n');

    expect(text('active')).toBe('none');
    expect(text('low')).toBe('false');
    expect(text('announcement')).toBe(
      'No low-confidence regions in this document'
    );
  });
});

describe('toggles', () => {
  it('toggles the bounding boxes with B and announces it', () => {
    renderViewer();
    expect(text('boxes')).toBe('true');

    press('b');
    expect(text('boxes')).toBe('false');
    expect(text('announcement')).toBe('Bounding boxes hidden');

    press('B');
    expect(text('boxes')).toBe('true');
    expect(text('announcement')).toBe('Bounding boxes shown');
  });

  it('toggles the thumbnail sidebar once per T press', () => {
    renderViewer();
    const before = text('thumbnails');

    press('t');
    expect(text('thumbnails')).not.toBe(before);
    press('t');
    expect(text('thumbnails')).toBe(before);
  });
});

describe('typing', () => {
  it('runs no shortcut while typing in the inline region editor', () => {
    renderViewer(
      FLAGGED_PAGES,
      <InlineRegionEditor
        documentId="doc-1"
        regionId="p1-clear"
        initialText="p1-clear"
        pageNumber={1}
        onDone={() => {}}
      />
    );
    const editor = screen.getByRole('textbox');
    expect(document.activeElement).toBe(editor);

    for (const key of ['j', 'ArrowRight', 'n', 'b', 't', '?']) {
      expect(press(key).defaultPrevented).toBe(false);
    }

    expect(text('page')).toBe('1');
    expect(text('active')).toBe('none');
    expect(text('boxes')).toBe('true');
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('help modal', () => {
  it('opens on ?, lists every shortcut, traps focus and restores it', () => {
    renderViewer();
    const trigger = screen.getByText('Activate clear region');
    trigger.focus();

    press('?', { shiftKey: true });

    const dialog = screen.getByRole('dialog', { name: 'Keyboard shortcuts' });
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(HTMLDialogElement.prototype.showModal).toHaveBeenCalled();
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(
      within(dialog).getByRole('button', { name: 'Close' })
    );

    const terms = within(dialog)
      .getAllByRole('term')
      .map((term) => term.textContent);
    expect(terms).toEqual(VIEWER_SHORTCUTS.map((item) => item.description));
    expect(
      within(dialog)
        .getAllByRole('definition')
        .map((definition) =>
          Array.from(definition.querySelectorAll('kbd')).map(
            (key) => key.textContent
          )
        )
    ).toEqual([
      ['→', 'J'],
      ['←', 'K'],
      ['N'],
      ['Shift+N'],
      ['B'],
      ['T'],
      ['?'],
    ]);

    expect(press('j').defaultPrevented).toBe(false);
    expect(text('page')).toBe('1');

    press('?', { shiftKey: true });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('closes on Escape and on the close button', () => {
    renderViewer();

    press('?');
    act(() => {
      fireEvent(
        screen.getByRole('dialog'),
        new Event('cancel', { cancelable: true })
      );
    });
    expect(screen.queryByRole('dialog')).toBeNull();

    press('?');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('leaves the keys alone while another modal dialog is open', () => {
    renderViewer();
    const other = document.createElement('div');
    other.setAttribute('role', 'dialog');
    other.setAttribute('aria-modal', 'true');
    document.body.appendChild(other);

    expect(press('?').defaultPrevented).toBe(false);
    expect(press('b').defaultPrevented).toBe(false);
    expect(
      screen.queryByRole('dialog', { name: 'Keyboard shortcuts' })
    ).toBeNull();
    expect(text('boxes')).toBe('true');
    other.remove();
  });
});
