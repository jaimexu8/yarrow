import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import type { DocumentSummary } from '@/lib/documents';
import {
  getDocumentProgress,
  PROGRESS_POLL_INTERVAL_MS,
  type DocumentProgress,
} from '@/lib/progress';
import {
  PROGRESS_FADE_MS,
  ProcessingProgressBar,
} from './ProcessingProgressBar';
import { ViewerProvider } from './ViewerContext';

vi.mock('@/lib/progress', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/progress')>()),
  getDocumentProgress: vi.fn(),
}));

const fetchProgress = vi.mocked(getDocumentProgress);

function progress(overrides: Partial<DocumentProgress> = {}): DocumentProgress {
  return {
    document_id: 'doc-1',
    document_status: 'processing',
    job_id: 'job-1',
    job_status: 'processing',
    current_stage: 'parsing',
    pages_total: 4,
    pages_completed: 0,
    pages_failed: 0,
    progress_percent: 0,
    updated_at: null,
    pages: [],
    ...overrides,
  };
}

function summary(status: string): DocumentSummary {
  return {
    id: 'doc-1',
    status,
    page_count: 4,
    reprocess: null,
  } as unknown as DocumentSummary;
}

function renderBar(status: string, refresh = vi.fn()) {
  const view = (documentStatus: string) => (
    <ViewerProvider
      document={summary(documentStatus)}
      tree={null}
      refresh={refresh}
    >
      <ProcessingProgressBar />
    </ViewerProvider>
  );
  const result = render(view(status));
  return {
    refresh,
    setStatus: (next: string) => result.rerender(view(next)),
  };
}

async function tick(ms = PROGRESS_POLL_INTERVAL_MS) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function bar() {
  return screen.getByRole('progressbar', { name: 'Processing progress' });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

test('updates as pages finish without refetching the document', async () => {
  fetchProgress
    .mockResolvedValueOnce(
      progress({ pages_completed: 1, progress_percent: 25 })
    )
    .mockResolvedValueOnce(
      progress({ pages_completed: 2, pages_failed: 1, progress_percent: 75 })
    );
  const { refresh } = renderBar('processing');

  await tick(0);
  expect(bar().getAttribute('aria-valuenow')).toBe('25');
  expect(screen.getByRole('status').textContent).toBe(
    '1 of 4 pages · Reading pages'
  );

  await tick();
  expect(bar().getAttribute('aria-valuenow')).toBe('75');
  expect(screen.getByRole('status').textContent).toBe(
    '3 of 4 pages · Reading pages'
  );
  expect(fetchProgress).toHaveBeenCalledTimes(2);
  expect(refresh).not.toHaveBeenCalled();
});

test('a failed poll keeps the last value and retries', async () => {
  fetchProgress
    .mockResolvedValueOnce(
      progress({ pages_completed: 2, progress_percent: 50 })
    )
    .mockRejectedValueOnce(new Error('network'))
    .mockResolvedValueOnce(
      progress({ pages_completed: 3, progress_percent: 75 })
    );
  renderBar('processing');

  await tick(0);
  await tick();
  expect(bar().getAttribute('aria-valuenow')).toBe('50');

  await tick();
  expect(bar().getAttribute('aria-valuenow')).toBe('75');
});

test('refreshes once when the job finishes, then stops polling', async () => {
  fetchProgress.mockResolvedValue(
    progress({
      job_status: 'completed',
      current_stage: 'finished',
      pages_completed: 4,
      progress_percent: 100,
    })
  );
  const { refresh } = renderBar('processing');

  await tick(0);
  await tick();
  await tick();

  expect(refresh).toHaveBeenCalledTimes(1);
  expect(fetchProgress).toHaveBeenCalledTimes(1);
  expect(bar().getAttribute('aria-valuenow')).toBe('100');
});

test('stays until the finished content loads, then fades out', async () => {
  fetchProgress.mockResolvedValue(
    progress({ job_status: 'completed', progress_percent: 100 })
  );
  const { setStatus } = renderBar('processing');
  await tick(0);

  expect(screen.getByTestId('processing-progress').className).toContain(
    'opacity-100'
  );

  setStatus('completed');
  expect(screen.getByTestId('processing-progress').className).toContain(
    'opacity-0'
  );

  await tick(PROGRESS_FADE_MS);
  expect(screen.queryByTestId('processing-progress')).toBeNull();
});

test('is not shown for a finished document', async () => {
  renderBar('completed');
  await tick();

  expect(screen.queryByRole('progressbar')).toBeNull();
  expect(fetchProgress).not.toHaveBeenCalled();
});

test('pauses while the tab is hidden', async () => {
  fetchProgress.mockResolvedValue(progress());
  const visibility = vi
    .spyOn(document, 'visibilityState', 'get')
    .mockReturnValue('visible');
  renderBar('processing');
  await tick(0);
  expect(fetchProgress).toHaveBeenCalledTimes(1);

  visibility.mockReturnValue('hidden');
  document.dispatchEvent(new Event('visibilitychange'));
  await tick();
  await tick();
  expect(fetchProgress).toHaveBeenCalledTimes(1);

  visibility.mockReturnValue('visible');
  document.dispatchEvent(new Event('visibilitychange'));
  await tick(0);
  expect(fetchProgress).toHaveBeenCalledTimes(2);
});
