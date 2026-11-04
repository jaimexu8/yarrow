/** US-81: choosing or dropping a folder on the Upload page. */
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
  type ResumableOutcome,
} from '@/lib/resumableUpload';
import {
  dirEntry,
  dropOf,
  fileAt,
  fileEntry,
  heldDirEntry,
  setFolderPickerSupport,
} from '@/lib/folderFiles.fixtures';
import { storeBatchId } from '@/lib/useBatchProgress';
import UploadPage from './page';

const api = vi.hoisted(() => ({
  batchIds: [] as string[],
  calls: [] as { name: string; batchId?: string }[],
}));

vi.mock('@/lib/documents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/documents')>()),
  newBatchId: () => api.batchIds.shift() ?? 'batch-extra',
  uploadDocument: async (
    file: File,
    _uploadId: string,
    _onProgress: (n: number) => void,
    batchId?: string
  ): Promise<UploadOutcome> => {
    api.calls.push({ name: file.name, batchId });
    if (file.name.startsWith('dup')) {
      // US-79: already in the library.
      return {
        kind: 'duplicate',
        reason: 'You already have an identical document in your library.',
        duplicateOf: { documentId: 'existing', filename: 'Lease.pdf' },
      };
    }
    return { kind: 'accepted', documentId: 'doc', processingStarted: true };
  },
}));

// Folder files take the same paths as any other file (US-80, US-60).
const merged = vi.hoisted(() => ({
  chunked: [] as { name: string; batchId?: string }[],
  seals: [] as string[],
}));

vi.mock('@/lib/resumableUpload', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/resumableUpload')>()),
  uploadResumable: async (
    file: File,
    options: { batchId?: string }
  ): Promise<ResumableOutcome> => {
    merged.chunked.push({ name: file.name, batchId: options.batchId });
    return { kind: 'accepted', documentId: 'big', processingStarted: true };
  },
}));

vi.mock('@/lib/batchNotifications', () => ({
  sealBatch: async (batchId: string) => {
    merged.seals.push(batchId);
  },
  announceBatchActivity: () => {},
}));

vi.mock('@/components/documents/BatchProgressPanel', () => ({
  BatchProgressPanel: (props: { batchId: string }) => (
    <div data-testid="panel">{props.batchId}</div>
  ),
}));

vi.mock('@/components/documents/DocumentList', () => ({
  DocumentList: () => null,
}));

function chooseFolder(...files: File[]) {
  const input = screen.getByTestId('folder-input') as HTMLInputElement;
  fireEvent.change(input, { target: { files } });
}

function dropZone() {
  return screen.getByText(/^Drag files/).parentElement as HTMLElement;
}

const nested = () => [
  fileAt('scans/top.pdf'),
  fileAt('scans/2024/mid.png', 'png'),
  fileAt('scans/2024/q1/deep.pdf'),
];

beforeEach(() => {
  api.batchIds = ['batch-1', 'batch-2'];
  api.calls = [];
  merged.chunked = [];
  merged.seals = [];
  setFolderPickerSupport(true);
});

afterEach(() => {
  cleanup();
  setFolderPickerSupport(false);
  act(() => storeBatchId(null));
});

describe('Upload page folders (US-81)', () => {
  // AC 1
  it('lists the documents from nested subfolders of a chosen folder', async () => {
    render(<UploadPage />);
    chooseFolder(...nested());

    expect(await screen.findByText('scans/top.pdf')).toBeTruthy();
    expect(screen.getByText('scans/2024/mid.png')).toBeTruthy();
    expect(screen.getByText('scans/2024/q1/deep.pdf')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Upload 3 files' })).toBeTruthy();
    expect(screen.getByText('Found 3 files in the folder.')).toBeTruthy();
  });

  // AC 1 (drag and drop)
  it('lists the documents from nested subfolders of a dropped folder', async () => {
    render(<UploadPage />);
    fireEvent.drop(dropZone(), {
      dataTransfer: dropOf([
        dirEntry('scans', [
          fileEntry('scans/top.pdf'),
          dirEntry('scans/2024', [
            fileEntry('scans/2024/mid.png'),
            dirEntry('scans/2024/q1', [fileEntry('scans/2024/q1/deep.pdf')]),
          ]),
        ]),
      ]),
    });

    expect(await screen.findByText('scans/2024/q1/deep.pdf')).toBeTruthy();
    expect(screen.getByText('scans/top.pdf')).toBeTruthy();
    expect(screen.getByText('scans/2024/mid.png')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Upload 3 files' })).toBeTruthy();
  });

  it('keeps same-named files from different subfolders apart', async () => {
    render(<UploadPage />);
    chooseFolder(fileAt('scans/a/receipt.pdf'), fileAt('scans/b/receipt.pdf'));

    expect(await screen.findByText('scans/a/receipt.pdf')).toBeTruthy();
    expect(screen.getByText('scans/b/receipt.pdf')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Upload 2 files' })).toBeTruthy();
  });

  it('does not add a folder twice when it is chosen again', async () => {
    render(<UploadPage />);
    chooseFolder(...nested());
    chooseFolder(...nested());
    await screen.findByText('scans/top.pdf');
    expect(screen.getAllByText('scans/top.pdf')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Upload 3 files' })).toBeTruthy();
  });

  // AC 2
  it('marks unsupported and empty files in a folder without blocking the rest', async () => {
    render(<UploadPage />);
    chooseFolder(
      fileAt('mixed/a.pdf'),
      fileAt('mixed/notes.txt', 'hello'),
      fileAt('mixed/sub/blank.pdf', ''),
      fileAt('mixed/sub/b.jpg', 'jpg'),
      fileAt('mixed/.DS_Store', 'x')
    );

    await screen.findByText('mixed/notes.txt');
    const row = (path: string) =>
      screen.getByText(path).closest('li')!.textContent;
    expect(row('mixed/notes.txt')).toContain(
      'Not accepted: Unsupported file type'
    );
    expect(row('mixed/sub/blank.pdf')).toContain('Not accepted: File is empty');
    expect(row('mixed/a.pdf')).toContain('Ready to upload');
    expect(row('mixed/sub/b.jpg')).toContain('Ready to upload');
    // The hidden system file is left out and mentioned, not listed.
    expect(screen.queryByText('mixed/.DS_Store')).toBeNull();
    expect(
      screen.getByText(/1 hidden or system item was skipped/)
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Upload 2 files' })).toBeTruthy();
  });

  // AC 3
  it('sends every accepted file of a folder in one batch', async () => {
    render(<UploadPage />);
    chooseFolder(...nested(), fileAt('scans/2024/readme.txt', 'x'));
    fireEvent.click(
      await screen.findByRole('button', { name: 'Upload 3 files' })
    );

    await waitFor(() => expect(api.calls).toHaveLength(3));
    expect(api.calls).toEqual([
      { name: 'top.pdf', batchId: 'batch-1' },
      { name: 'mid.png', batchId: 'batch-1' },
      { name: 'deep.pdf', batchId: 'batch-1' },
    ]);
    await waitFor(() =>
      expect(screen.getByTestId('panel').textContent).toBe('batch-1')
    );
    expect(
      await screen.findByText(
        /3 files uploaded and queued for processing, 1 not accepted/
      )
    ).toBeTruthy();
  });

  it('sends large folder files in chunks, all in one sealed batch (US-80, US-60)', async () => {
    const big = fileAt('scans/2024/big.pdf');
    Object.defineProperty(big, 'size', {
      value: RESUMABLE_THRESHOLD_BYTES + 1,
    });
    render(<UploadPage />);
    chooseFolder(fileAt('scans/top.pdf'), big);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Upload 2 files' })
    );

    await waitFor(() => expect(merged.seals).toEqual(['batch-1']));
    expect(api.calls).toEqual([{ name: 'top.pdf', batchId: 'batch-1' }]);
    expect(merged.chunked).toEqual([{ name: 'big.pdf', batchId: 'batch-1' }]);
  });

  it('warns about a duplicate folder file by its path (US-79)', async () => {
    render(<UploadPage />);
    chooseFolder(fileAt('scans/top.pdf'), fileAt('scans/2024/dup.pdf'));
    fireEvent.click(
      await screen.findByRole('button', { name: 'Upload 2 files' })
    );

    expect(
      await screen.findByRole('button', { name: 'Skip scans/2024/dup.pdf' })
    ).toBeTruthy();
    expect(
      screen.getByRole('button', { name: 'Upload scans/2024/dup.pdf anyway' })
    ).toBeTruthy();
    // The run still seals: top.pdf joined it.
    await waitFor(() => expect(merged.seals).toEqual(['batch-1']));
  });

  // AC 3 (drag and drop)
  it('sends a dropped folder and loose files together in one batch', async () => {
    render(<UploadPage />);
    fireEvent.drop(dropZone(), {
      dataTransfer: dropOf([
        dirEntry('scans', [fileEntry('scans/a.pdf'), fileEntry('scans/b.pdf')]),
        fileEntry('loose.pdf'),
      ]),
    });
    fireEvent.click(
      await screen.findByRole('button', { name: 'Upload 3 files' })
    );
    await waitFor(() => expect(api.calls).toHaveLength(3));
    expect(new Set(api.calls.map((call) => call.batchId))).toEqual(
      new Set(['batch-1'])
    );
  });

  it('keeps Upload disabled while a dropped folder is being read', async () => {
    render(<UploadPage />);
    chooseFolder(fileAt('loose/first.pdf'));
    const held = heldDirEntry('scans', [
      fileEntry('scans/a.pdf'),
      fileEntry('scans/b.pdf'),
    ]);
    fireEvent.drop(dropZone(), { dataTransfer: dropOf([held.entry]) });

    expect(await screen.findByText('Reading folder…')).toBeTruthy();
    const upload = screen.getByRole('button', { name: 'Upload 1 file' });
    expect(upload).toHaveProperty('disabled', true);
    fireEvent.click(upload);
    expect(api.calls).toEqual([]);

    await act(async () => held.release());
    const ready = await screen.findByRole('button', { name: 'Upload 3 files' });
    expect(ready).toHaveProperty('disabled', false);
    expect(screen.queryByText('Reading folder…')).toBeNull();
  });

  it('sends the files of a slow drop in the same batch as the rest', async () => {
    render(<UploadPage />);
    chooseFolder(fileAt('loose/first.pdf'));
    const held = heldDirEntry('scans', [
      fileEntry('scans/a.pdf'),
      dirEntry('scans/sub', [fileEntry('scans/sub/b.pdf')]),
    ]);
    fireEvent.drop(dropZone(), { dataTransfer: dropOf([held.entry]) });
    await screen.findByText('Reading folder…');
    // Clicking while the folder is read sends nothing.
    fireEvent.click(screen.getByRole('button', { name: 'Upload 1 file' }));

    await act(async () => held.release());
    fireEvent.click(
      await screen.findByRole('button', { name: 'Upload 3 files' })
    );
    await waitFor(() => expect(api.calls).toHaveLength(3));
    expect(api.calls).toEqual([
      { name: 'first.pdf', batchId: 'batch-1' },
      { name: 'a.pdf', batchId: 'batch-1' },
      { name: 'b.pdf', batchId: 'batch-1' },
    ]);
  });

  it('does not refill a cleared list with a folder read that finishes later', async () => {
    render(<UploadPage />);
    chooseFolder(fileAt('loose/first.pdf'));
    const held = heldDirEntry('scans', [fileEntry('scans/a.pdf')]);
    fireEvent.drop(dropZone(), { dataTransfer: dropOf([held.entry]) });
    await screen.findByText('Reading folder…');

    fireEvent.click(screen.getByRole('button', { name: 'Clear list' }));
    expect(screen.queryByText('Reading folder…')).toBeNull();
    await act(async () => held.release());
    // Give the discarded read every chance to land.
    await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));

    expect(screen.queryByText('scans/a.pdf')).toBeNull();
    expect(screen.queryByText('loose/first.pdf')).toBeNull();
    expect(screen.queryByRole('listitem')).toBeNull();

    // A new drop after the Clear is read as usual.
    fireEvent.drop(dropZone(), {
      dataTransfer: dropOf([dirEntry('next', [fileEntry('next/c.pdf')])]),
    });
    expect(await screen.findByText('next/c.pdf')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Upload 1 file' })).toBeTruthy();
  });

  it('says so when a chosen folder is empty', async () => {
    render(<UploadPage />);
    chooseFolder();
    expect(
      await screen.findByText('That folder has no files to upload.')
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Upload files' })).toHaveProperty(
      'disabled',
      true
    );
  });

  it('says so when a dropped folder is empty', async () => {
    render(<UploadPage />);
    fireEvent.drop(dropZone(), {
      dataTransfer: dropOf([dirEntry('empty', [dirEntry('empty/sub', [])])]),
    });
    expect(
      await screen.findByText('That folder has no files to upload.')
    ).toBeTruthy();
    expect(screen.queryByRole('listitem')).toBeNull();
  });
});

// AC 4
describe('Upload page without folder support (US-81)', () => {
  beforeEach(() => setFolderPickerSupport(false));

  it('hides the folder picker and keeps ordinary file selection working', async () => {
    render(<UploadPage />);
    expect(screen.queryByText('Choose folder')).toBeNull();
    expect(screen.queryByTestId('folder-input')).toBeNull();
    expect(
      screen.getByText('Choose files', { selector: 'label' })
    ).toBeTruthy();

    const input = document.querySelector(
      'input[type=file]'
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [new File(['%PDF'], 'a.pdf'), new File(['x'], 'b.pdf')],
      },
    });
    fireEvent.click(
      await screen.findByRole('button', { name: 'Upload 2 files' })
    );
    await waitFor(() => expect(api.calls).toHaveLength(2));
    expect(api.calls.every((call) => call.batchId === 'batch-1')).toBe(true);
  });

  it('still accepts dropped files when webkitGetAsEntry is missing', async () => {
    render(<UploadPage />);
    fireEvent.drop(dropZone(), {
      dataTransfer: {
        files: [new File(['%PDF'], 'a.pdf')],
        items: [{ kind: 'file', type: 'application/pdf' }],
      },
    });
    expect(await screen.findByText('a.pdf')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Upload 1 file' })).toBeTruthy();
  });
});
