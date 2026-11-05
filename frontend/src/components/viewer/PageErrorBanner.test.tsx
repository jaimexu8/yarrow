import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { PageState } from '@/lib/pageStatus';
import { PageErrorBanner } from './PageErrorBanner';

const { viewer, reprocess } = vi.hoisted(() => ({
  viewer: {
    document: { id: 'doc-1' },
    canEdit: true,
    pageStates: new Map<number, PageState>(),
    refresh: () => {},
  },
  reprocess: {
    available: true,
    request: vi.fn(),
    reprocessing: false,
    error: null as string | null,
    dialog: null,
  },
}));

vi.mock('./ViewerContext', () => ({ useViewer: () => viewer }));
vi.mock('@/components/documents/documentActions', () => ({
  useReprocessAction: () => reprocess,
}));

beforeEach(() => {
  viewer.canEdit = true;
  reprocess.available = true;
  viewer.pageStates = new Map([
    [
      2,
      {
        status: 'failed',
        error_message:
          'The text recognition service took too long on this page.',
      },
    ],
    [3, { status: 'completed', error_message: null }],
  ]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test('explains why a failed page could not be processed', () => {
  render(<PageErrorBanner pageNumber={2} />);
  const banner = screen.getByRole('alert');
  expect(banner.textContent).toContain('Page 2 could not be processed');
  expect(banner.textContent).toContain(
    'The text recognition service took too long on this page.'
  );
});

test('renders nothing for a page that did not fail', () => {
  const { container } = render(<PageErrorBanner pageNumber={3} />);
  expect(container.innerHTML).toBe('');
  render(<PageErrorBanner pageNumber={9} />);
  expect(screen.queryByRole('alert')).toBeNull();
});

test('offers the reprocess action to editors', () => {
  render(<PageErrorBanner pageNumber={2} />);
  fireEvent.click(screen.getByRole('button', { name: 'Reprocess' }));
  expect(reprocess.request).toHaveBeenCalledTimes(1);
});

test('hides the reprocess action from readers', () => {
  viewer.canEdit = false;
  render(<PageErrorBanner pageNumber={2} />);
  expect(screen.getByRole('alert')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Reprocess' })).toBeNull();
});

test('hides the reprocess action while a job is running', () => {
  reprocess.available = false;
  render(<PageErrorBanner pageNumber={2} />);
  expect(screen.queryByRole('button', { name: 'Reprocess' })).toBeNull();
});
