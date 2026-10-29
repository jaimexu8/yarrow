/** US-31: the progress panel shows accurate per-status counts for a run. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { BatchProgress } from '@/lib/documents';
import { BatchProgressPanel, batchSummary } from './BatchProgressPanel';

const state = vi.hoisted(() => ({
  progress: null as BatchProgress | null,
  error: null as string | null,
  gone: false,
}));

vi.mock('@/lib/useBatchProgress', () => ({
  useBatchProgress: () => state,
}));

function progress(overrides: Partial<BatchProgress> = {}): BatchProgress {
  return {
    batch_id: 'batch-1',
    total: 5,
    queued: 1,
    processing: 1,
    completed: 2,
    failed: 1,
    canceled: 0,
    created_at: '2026-10-27T10:00:00',
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  state.progress = null;
  state.error = null;
  state.gone = false;
});

describe('batchSummary', () => {
  it('reports progress while files are still being worked on', () => {
    expect(batchSummary(progress())).toBe('3 of 5 processed, 1 failed');
  });

  it('never calls the run finished while files are still uploading', () => {
    const done = progress({
      total: 1,
      queued: 0,
      processing: 0,
      completed: 1,
      failed: 0,
    });
    expect(batchSummary(done, true)).toBe('1 of 1 processed, still uploading');
    expect(batchSummary(done)).toBe('All 1 file finished');
  });

  it('reports a finished run with its failures and cancellations', () => {
    expect(
      batchSummary(
        progress({
          total: 4,
          queued: 0,
          processing: 0,
          completed: 2,
          failed: 1,
          canceled: 1,
        })
      )
    ).toBe('All 4 files finished, 1 failed, 1 canceled');
  });
});

describe('BatchProgressPanel', () => {
  it('shows a count for every status', () => {
    state.progress = progress();
    render(
      <BatchProgressPanel
        batchId="batch-1"
        refreshKey={0}
        onDismiss={() => {}}
      />
    );

    for (const [label, count] of [
      ['Queued', '1'],
      ['Processing', '1'],
      ['Completed', '2'],
      ['Failed', '1'],
      ['Canceled', '0'],
    ]) {
      const term = screen.getByText(label);
      expect(term.nextElementSibling?.textContent).toBe(count);
    }
    expect(screen.getByRole('progressbar')).toHaveProperty('ariaValueNow', '3');
  });

  it('says it is loading until the first counts arrive', () => {
    render(
      <BatchProgressPanel
        batchId="batch-1"
        refreshKey={0}
        onDismiss={() => {}}
      />
    );
    expect(screen.getByText('Loading progress…')).toBeTruthy();
  });

  it('keeps the panel and shows the error when a read fails', () => {
    state.error = 'Network error';
    render(
      <BatchProgressPanel
        batchId="batch-1"
        refreshKey={0}
        onDismiss={() => {}}
      />
    );
    expect(
      screen.getByText('Could not load progress: Network error')
    ).toBeTruthy();
  });

  it('renders nothing once the server no longer knows the batch', () => {
    state.gone = true;
    const { container } = render(
      <BatchProgressPanel
        batchId="batch-1"
        refreshKey={0}
        onDismiss={() => {}}
      />
    );
    expect(container.innerHTML).toBe('');
  });

  it('can be dismissed', () => {
    state.progress = progress();
    const onDismiss = vi.fn();
    render(
      <BatchProgressPanel
        batchId="batch-1"
        refreshKey={0}
        onDismiss={onDismiss}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
