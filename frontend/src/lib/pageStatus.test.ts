import { expect, test } from 'vitest';
import { mergePageStates } from './pageStatus';
import type { DocumentProgress, PageProgress } from './progress';
import type { PageNode } from './viewer';

function treePage(
  page_number: number,
  status: string | null,
  error_message: string | null = null
): PageNode {
  return {
    page_number,
    width: null,
    height: null,
    status,
    error_message,
    has_thumbnail: false,
    image_key: null,
    regions: [],
    warnings: [],
  };
}

function progressPage(
  page_number: number,
  status: PageProgress['status'],
  error_message: string | null = null
): PageProgress {
  return {
    page_number,
    status,
    error_code: null,
    error_message,
    started_at: null,
    finished_at: null,
  };
}

function progress(pages: PageProgress[]): DocumentProgress {
  return {
    document_id: 'doc-1',
    document_status: 'processing',
    job_id: 'job-1',
    job_status: 'processing',
    current_stage: 'parsing',
    pages_total: pages.length,
    pages_completed: 0,
    pages_failed: 0,
    progress_percent: 0,
    updated_at: null,
    pages,
  };
}

test('uses the stored page status when there is no live progress', () => {
  const states = mergePageStates(
    [treePage(1, 'completed'), treePage(2, 'failed', 'Could not read.')],
    null
  );
  expect(states.get(1)).toEqual({ status: 'completed', error_message: null });
  expect(states.get(2)).toEqual({
    status: 'failed',
    error_message: 'Could not read.',
  });
});

test('live progress wins over the stored page status', () => {
  const states = mergePageStates(
    [treePage(1, 'completed'), treePage(2, 'failed', 'Old reason.')],
    progress([
      progressPage(1, 'completed'),
      progressPage(2, 'processing'),
      progressPage(3, 'queued'),
    ])
  );
  expect(states.get(2)).toEqual({ status: 'processing', error_message: null });
  expect(states.get(3)).toEqual({ status: 'queued', error_message: null });
});

test('ignores stored statuses outside the page vocabulary', () => {
  const states = mergePageStates([treePage(1, 'mystery')], null);
  expect(states.get(1)?.status).toBeNull();
});
