/** US-31: how the Upload page ties each file to its run (batch). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import type { UploadOutcome } from '@/lib/documents';
import { storeBatchId } from '@/lib/useBatchProgress';
import UploadPage from './page';

const api = vi.hoisted(() => ({
  batchIds: [] as string[],
  calls: [] as { name: string; batchId?: string }[],
  // Each upload waits for the test to settle it with an outcome.
  pending: [] as ((outcome: UploadOutcome) => void)[],
}));

vi.mock('@/lib/documents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/documents')>()),
  newBatchId: () => api.batchIds.shift() ?? 'batch-extra',
  uploadDocument: (
    file: File,
    _uploadId: string,
    _onProgress: (n: number) => void,
    batchId?: string
  ) => {
    api.calls.push({ name: file.name, batchId });
    return new Promise<UploadOutcome>((resolve) => api.pending.push(resolve));
  },
}));

// The panel's own behaviour is tested separately; here only what the page
// hands it matters.
vi.mock('@/components/documents/BatchProgressPanel', () => ({
  BatchProgressPanel: (props: { batchId: string; uploading?: boolean }) => (
    <div data-testid="panel">
      {props.batchId} uploading={String(Boolean(props.uploading))}
    </div>
  ),
}));

vi.mock('@/components/documents/DocumentList', () => ({
  DocumentList: () => null,
}));

const accepted: UploadOutcome = {
  kind: 'accepted',
  documentId: 'doc',
  processingStarted: true,
};
const failed: UploadOutcome = { kind: 'failed', message: 'Network error' };

function pdf(name: string) {
  return new File(['%PDF-1.4'], name, { type: 'application/pdf' });
}

function choose(...files: File[]) {
  const input = document.querySelector('input[type=file]') as HTMLInputElement;
  fireEvent.change(input, { target: { files } });
}

async function settleNext(outcome: UploadOutcome) {
  await waitFor(() => expect(api.pending.length).toBeGreaterThan(0));
  await act(async () => api.pending.shift()!(outcome));
}

beforeEach(() => {
  api.batchIds = ['batch-1', 'batch-2'];
  api.calls = [];
  api.pending = [];
});

afterEach(() => {
  cleanup();
  act(() => storeBatchId(null));
});

describe('Upload page batches', () => {
  it('tells the panel the run is still uploading', async () => {
    render(<UploadPage />);
    choose(pdf('a.pdf'), pdf('b.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 2 files' }));

    await settleNext(accepted);
    // a.pdf is in, b.pdf is still on its way: the run is not finished.
    await waitFor(() =>
      expect(screen.getByTestId('panel').textContent).toBe(
        'batch-1 uploading=true'
      )
    );

    await settleNext(accepted);
    await waitFor(() =>
      expect(screen.getByTestId('panel').textContent).toBe(
        'batch-1 uploading=false'
      )
    );
  });

  it('retries a failed file into the run it was first sent in', async () => {
    render(<UploadPage />);
    choose(pdf('a.pdf'), pdf('b.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 2 files' }));
    await settleNext(failed); // a.pdf
    await settleNext(accepted); // b.pdf

    // A second run takes over the panel.
    choose(pdf('c.pdf'));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Upload 1 file' })).toBeTruthy()
    );
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));
    await settleNext(accepted);
    await waitFor(() =>
      expect(screen.getByTestId('panel').textContent).toContain('batch-2')
    );

    fireEvent.click(screen.getByRole('button', { name: 'Retry a.pdf' }));
    await settleNext(accepted);

    expect(api.calls).toEqual([
      { name: 'a.pdf', batchId: 'batch-1' },
      { name: 'b.pdf', batchId: 'batch-1' },
      { name: 'c.pdf', batchId: 'batch-2' },
      { name: 'a.pdf', batchId: 'batch-1' },
    ]);
    // The panel keeps showing the run on screen.
    expect(screen.getByTestId('panel').textContent).toContain('batch-2');
  });
});
