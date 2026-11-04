/**
 * US-31: how the Upload page ties each file to its run (batch).
 * US-79: the duplicate warning, Skip and Upload anyway.
 * US-80: large files go through the resumable upload, with Pause, Resume and
 * Retry.
 */
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
import {
  RESUMABLE_THRESHOLD_BYTES,
  SESSIONS_STORAGE_KEY,
  type ResumableOutcome,
} from '@/lib/resumableUpload';
import { storeBatchId } from '@/lib/useBatchProgress';
import UploadPage from './page';

const api = vi.hoisted(() => ({
  batchIds: [] as string[],
  calls: [] as { name: string; batchId?: string; allowDuplicate?: true }[],
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
    batchId?: string,
    allowDuplicate?: boolean
  ) => {
    api.calls.push({
      name: file.name,
      batchId,
      // Only recorded when set, so the batch tests' expectations stay short.
      ...(allowDuplicate ? { allowDuplicate: true as const } : {}),
    });
    return new Promise<UploadOutcome>((resolve) => api.pending.push(resolve));
  },
}));

const chunked = vi.hoisted(() => ({
  calls: [] as { name: string; batchId?: string; allowDuplicate?: true }[],
  pending: [] as ((outcome: ResumableOutcome) => void)[],
  discarded: [] as string[],
}));

vi.mock('@/lib/resumableUpload', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/resumableUpload')>()),
  uploadResumable: (
    file: File,
    options: {
      batchId?: string;
      signal?: AbortSignal;
      allowDuplicate?: boolean;
    }
  ) => {
    chunked.calls.push({
      name: file.name,
      batchId: options.batchId,
      // Only recorded when set, like the single-request mock above.
      ...(options.allowDuplicate ? { allowDuplicate: true as const } : {}),
    });
    return new Promise<ResumableOutcome>((resolve) => {
      chunked.pending.push(resolve);
      // What the real upload does when Pause aborts it.
      options.signal?.addEventListener('abort', () => {
        chunked.pending = chunked.pending.filter((other) => other !== resolve);
        resolve({ kind: 'paused' });
      });
    });
  },
  discardResumable: async (file: File) => {
    chunked.discarded.push(file.name);
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

const notifier = vi.hoisted(() => ({
  log: [] as string[],
}));

vi.mock('@/lib/batchNotifications', () => ({
  sealBatch: vi.fn(async (batchId: string) => {
    notifier.log.push(`seal ${batchId}`);
  }),
  announceBatchActivity: vi.fn(() => notifier.log.push('nudge')),
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
const duplicate: UploadOutcome = {
  kind: 'duplicate',
  reason: 'You already have an identical document in your library: Lease.pdf.',
  duplicateOf: { documentId: 'existing-doc', filename: 'Lease.pdf' },
};

function pdf(name: string) {
  return new File(['%PDF-1.4'], name, { type: 'application/pdf' });
}

/** Big enough for the resumable path; the bytes themselves don't matter. */
function largePdf(name: string) {
  const file = pdf(name);
  Object.defineProperty(file, 'size', {
    value: RESUMABLE_THRESHOLD_BYTES + 1,
  });
  return file;
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
  chunked.calls = [];
  chunked.pending = [];
  chunked.discarded = [];
  localStorage.clear();
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

  it('seals the run only after every file has been sent (US-60)', async () => {
    notifier.log = [];
    render(<UploadPage />);
    choose(pdf('a.pdf'), pdf('b.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 2 files' }));

    await settleNext(accepted);
    // a.pdf may already be processed, but b.pdf has not been sent yet.
    expect(notifier.log).toEqual([]);

    await settleNext(failed);
    await waitFor(() =>
      expect(notifier.log).toEqual(['seal batch-1', 'nudge'])
    );
  });

  it('does not seal a run that no file joined (US-60)', async () => {
    notifier.log = [];
    render(<UploadPage />);
    choose(pdf('a.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));
    await settleNext(failed);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Retry a.pdf' })).toBeTruthy()
    );
    expect(notifier.log).toEqual([]);
  });
});

describe('Upload page duplicates (US-79)', () => {
  async function uploadOneDuplicate(name = 'lease-copy.pdf') {
    render(<UploadPage />);
    choose(pdf(name));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));
    await settleNext(duplicate);
  }

  it('warns on the file row and names my existing document', async () => {
    await uploadOneDuplicate();

    expect(await screen.findByText(/Already in your library/)).toBeTruthy();
    const existing = screen.getByRole('link', { name: 'Lease.pdf' });
    expect(existing.getAttribute('href')).toBe('/documents/existing-doc');
    expect(
      screen.getByRole('button', { name: 'Skip lease-copy.pdf' })
    ).toBeTruthy();
    expect(
      screen.getByRole('button', { name: 'Upload lease-copy.pdf anyway' })
    ).toBeTruthy();
    // The warning is not a Retry: retrying would only be warned again.
    expect(
      screen.queryByRole('button', { name: 'Retry lease-copy.pdf' })
    ).toBeNull();
    expect(screen.getByRole('status').textContent).toContain(
      '1 already in your library'
    );
  });

  it('Skip sends nothing and marks the file skipped', async () => {
    await uploadOneDuplicate();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Skip lease-copy.pdf' })
    );

    expect(await screen.findByText(/Skipped/)).toBeTruthy();
    expect(api.calls).toHaveLength(1);
    expect(api.pending).toHaveLength(0);
    expect(
      screen.queryByRole('button', { name: 'Upload lease-copy.pdf anyway' })
    ).toBeNull();
    // A skipped file can still be taken off the list.
    expect(
      screen.getByRole('button', { name: 'Remove lease-copy.pdf' })
    ).toBeTruthy();
  });

  it('Upload anyway re-sends the file with the flag into the same run', async () => {
    await uploadOneDuplicate();

    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Upload lease-copy.pdf anyway',
      })
    );
    await settleNext(accepted);

    expect(api.calls).toEqual([
      { name: 'lease-copy.pdf', batchId: 'batch-1' },
      { name: 'lease-copy.pdf', batchId: 'batch-1', allowDuplicate: true },
    ]);
    expect(
      await screen.findByText(/Uploaded, queued for processing/)
    ).toBeTruthy();
    // The copy is now part of the run, so its progress is shown.
    expect(screen.getByTestId('panel').textContent).toContain('batch-1');
  });

  it('a Retry after a failed Upload anyway still asks for a copy', async () => {
    await uploadOneDuplicate();

    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Upload lease-copy.pdf anyway',
      })
    );
    await settleNext(failed);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Retry lease-copy.pdf' })
    );
    await settleNext(accepted);

    expect(api.calls).toEqual([
      { name: 'lease-copy.pdf', batchId: 'batch-1' },
      { name: 'lease-copy.pdf', batchId: 'batch-1', allowDuplicate: true },
      { name: 'lease-copy.pdf', batchId: 'batch-1', allowDuplicate: true },
    ]);
    expect(
      await screen.findByText(/Uploaded, queued for processing/)
    ).toBeTruthy();
  });

  it('a plain Retry of a file never warned about is not a copy', async () => {
    render(<UploadPage />);
    choose(pdf('a.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));
    await settleNext(failed);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry a.pdf' }));
    await settleNext(accepted);

    expect(api.calls).toEqual([
      { name: 'a.pdf', batchId: 'batch-1' },
      { name: 'a.pdf', batchId: 'batch-1' },
    ]);
  });

  it('a duplicate in a run does not hold up the other files', async () => {
    render(<UploadPage />);
    choose(pdf('a.pdf'), pdf('b.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 2 files' }));
    await settleNext(duplicate); // a.pdf
    await settleNext(accepted); // b.pdf

    expect(api.calls).toEqual([
      { name: 'a.pdf', batchId: 'batch-1' },
      { name: 'b.pdf', batchId: 'batch-1' },
    ]);
    expect(screen.getByRole('button', { name: 'Skip a.pdf' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Skip b.pdf' })).toBeNull();
  });
});

describe('Upload page resumable uploads', () => {
  async function settleChunked(outcome: ResumableOutcome) {
    await waitFor(() => expect(chunked.pending.length).toBeGreaterThan(0));
    await act(async () => chunked.pending.shift()!(outcome));
  }

  it('sends a large file in chunks and a small one in one request', async () => {
    render(<UploadPage />);
    choose(largePdf('big.pdf'), pdf('small.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 2 files' }));

    await settleChunked(accepted);
    await settleNext(accepted);

    expect(chunked.calls).toEqual([{ name: 'big.pdf', batchId: 'batch-1' }]);
    expect(api.calls).toEqual([{ name: 'small.pdf', batchId: 'batch-1' }]);
    await waitFor(() =>
      expect(screen.getByTestId('panel').textContent).toBe(
        'batch-1 uploading=false'
      )
    );
  });

  it('pauses a large file and resumes it into the same run', async () => {
    render(<UploadPage />);
    choose(largePdf('big.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));
    await waitFor(() => expect(chunked.calls.length).toBe(1));

    fireEvent.click(screen.getByRole('button', { name: 'Pause big.pdf' }));
    const resume = await screen.findByRole('button', {
      name: 'Resume big.pdf',
    });
    expect(screen.getByText(/Paused/)).toBeTruthy();
    // The partly sent file keeps its progress bar while paused.
    expect(
      screen.getByRole('progressbar', { name: 'Uploading big.pdf' })
    ).toBeTruthy();

    fireEvent.click(resume);
    await waitFor(() => expect(chunked.calls.length).toBe(2));
    expect(chunked.calls[1]).toEqual({ name: 'big.pdf', batchId: 'batch-1' });
    await settleChunked(accepted);
    await screen.findByText(/Uploaded, queued for processing/);
    expect(screen.queryByRole('button', { name: 'Resume big.pdf' })).toBeNull();
  });

  it('offers Retry when a large file fails, and Retry resumes it', async () => {
    render(<UploadPage />);
    choose(largePdf('big.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));
    await settleChunked({ kind: 'failed', message: 'Network error' });

    fireEvent.click(
      await screen.findByRole('button', { name: 'Retry big.pdf' })
    );
    await settleChunked(accepted);

    expect(chunked.calls).toEqual([
      { name: 'big.pdf', batchId: 'batch-1' },
      { name: 'big.pdf', batchId: 'batch-1' },
    ]);
    await screen.findByText(/Uploaded, queued for processing/);
  });

  it('removing a paused file discards what the server holds of it', async () => {
    render(<UploadPage />);
    choose(largePdf('big.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));
    await waitFor(() => expect(chunked.calls.length).toBe(1));
    fireEvent.click(screen.getByRole('button', { name: 'Pause big.pdf' }));

    fireEvent.click(
      await screen.findByRole('button', { name: 'Remove big.pdf' })
    );
    expect(chunked.discarded).toEqual(['big.pdf']);
  });

  it('points out an unfinished upload left from an earlier visit', async () => {
    localStorage.setItem(
      SESSIONS_STORAGE_KEY,
      JSON.stringify({
        'big.pdf:9000000:1': {
          sessionId: 'session-1',
          filename: 'big.pdf',
          size: 9000000,
        },
      })
    );
    render(<UploadPage />);
    expect(
      await screen.findByText(/An upload did not finish: big\.pdf/)
    ).toBeTruthy();
  });

  it('warns about a large duplicate; Skip discards its chunks (US-79)', async () => {
    render(<UploadPage />);
    choose(largePdf('big.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));
    await settleChunked(duplicate);

    expect(await screen.findByText(/Already in your library/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Lease.pdf' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Skip big.pdf' }));

    expect(await screen.findByText(/Skipped/)).toBeTruthy();
    // The session the server kept for Upload anyway is thrown away.
    expect(chunked.discarded).toEqual(['big.pdf']);
    expect(chunked.calls).toHaveLength(1);
    expect(api.calls).toEqual([]);
  });

  it('Upload anyway finishes a large duplicate on the resumable path', async () => {
    render(<UploadPage />);
    choose(largePdf('big.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));
    await settleChunked(duplicate);

    fireEvent.click(
      await screen.findByRole('button', { name: 'Upload big.pdf anyway' })
    );
    await settleChunked(accepted);

    expect(chunked.calls).toEqual([
      { name: 'big.pdf', batchId: 'batch-1' },
      { name: 'big.pdf', batchId: 'batch-1', allowDuplicate: true },
    ]);
    // Never sent as a single request, and its session was kept.
    expect(api.calls).toEqual([]);
    expect(chunked.discarded).toEqual([]);
    await screen.findByText(/Uploaded, queued for processing/);
  });

  it('seals a run of large, paused and duplicate files once the loop ends (US-60)', async () => {
    notifier.log = [];
    render(<UploadPage />);
    choose(largePdf('big.pdf'), largePdf('later.pdf'), pdf('copy.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 3 files' }));

    await settleChunked(accepted); // big.pdf, through the resumable path
    await waitFor(() => expect(chunked.calls.length).toBe(2));
    fireEvent.click(screen.getByRole('button', { name: 'Pause later.pdf' }));
    await settleNext(duplicate); // copy.pdf
    // Sealed once, after the last file, even though one is paused and one
    // was a duplicate: only big.pdf joined the run.
    await waitFor(() =>
      expect(notifier.log).toEqual(['seal batch-1', 'nudge'])
    );

    // Resuming the paused file joins it to the run, which is sealed again.
    fireEvent.click(
      await screen.findByRole('button', { name: 'Resume later.pdf' })
    );
    await settleChunked(accepted);
    await waitFor(() =>
      expect(notifier.log).toEqual([
        'seal batch-1',
        'nudge',
        'seal batch-1',
        'nudge',
      ])
    );
    expect(chunked.calls.at(-1)).toEqual({
      name: 'later.pdf',
      batchId: 'batch-1',
    });
  });

  it('seals the run when Upload anyway joins a large duplicate to it (US-60)', async () => {
    notifier.log = [];
    render(<UploadPage />);
    choose(largePdf('big.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));
    await settleChunked(duplicate);
    await screen.findByRole('button', { name: 'Upload big.pdf anyway' });
    // Nothing joined the run, so there is nothing to seal yet.
    expect(notifier.log).toEqual([]);

    fireEvent.click(
      screen.getByRole('button', { name: 'Upload big.pdf anyway' })
    );
    await settleChunked(accepted);
    await waitFor(() =>
      expect(notifier.log).toEqual(['seal batch-1', 'nudge'])
    );
  });
});
