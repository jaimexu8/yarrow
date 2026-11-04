/** US-81: finding the files inside a chosen or dropped folder. */
import { afterEach, describe, expect, it } from 'vitest';
import {
  MAX_FOLDER_FILES,
  isIgnoredPath,
  readAllEntries,
  scanDataTransfer,
  scanFileList,
  supportsFolderPicker,
} from './folderFiles';
import {
  dirEntry,
  dropOf,
  fileAt,
  fileEntry,
  setFolderPickerSupport,
  type FakeEntry,
} from './folderFiles.fixtures';

function drop(entries: FakeEntry[], files: File[] = []) {
  return scanDataTransfer(dropOf(entries, files) as unknown as DataTransfer);
}

const paths = (scan: { files: { path: string }[] }) =>
  scan.files.map((found) => found.path);

afterEach(() => setFolderPickerSupport(false));

describe('scanFileList (folder picker)', () => {
  it('keeps files from nested subfolders three levels deep, with their paths', () => {
    const scan = scanFileList([
      fileAt('scans/top.pdf'),
      fileAt('scans/2024/mid.png'),
      fileAt('scans/2024/q1/deep.pdf'),
    ]);
    expect(paths(scan)).toEqual([
      'scans/top.pdf',
      'scans/2024/mid.png',
      'scans/2024/q1/deep.pdf',
    ]);
    expect(scan.folders).toBe(1);
    expect(scan.files[2].file.name).toBe('deep.pdf');
  });

  it('keeps unsupported types for validation, but skips hidden and system files', () => {
    const scan = scanFileList([
      fileAt('scans/a.pdf'),
      fileAt('scans/notes.txt'),
      fileAt('scans/.DS_Store'),
      fileAt('scans/sub/Thumbs.db'),
      fileAt('scans/.git/config'),
    ]);
    expect(paths(scan)).toEqual(['scans/a.pdf', 'scans/notes.txt']);
    expect(scan.skipped).toBe(3);
  });

  it('treats ordinary picks as loose files named by their file name', () => {
    const scan = scanFileList([new File(['x'], 'a.pdf')]);
    expect(paths(scan)).toEqual(['a.pdf']);
    expect(scan.folders).toBe(0);
  });

  it('stops at the file cap and says so', () => {
    const files = Array.from({ length: MAX_FOLDER_FILES + 3 }, (_, i) =>
      fileAt(`big/f${i}.pdf`)
    );
    const scan = scanFileList(files);
    expect(scan.files).toHaveLength(MAX_FOLDER_FILES);
    expect(scan.truncated).toBe(true);
  });
});

describe('scanDataTransfer (drag and drop)', () => {
  it('walks dropped folders three levels deep', async () => {
    const scan = await drop([
      dirEntry('scans', [
        fileEntry('scans/top.pdf'),
        dirEntry('scans/2024', [
          fileEntry('scans/2024/mid.png'),
          dirEntry('scans/2024/q1', [fileEntry('scans/2024/q1/deep.pdf')]),
        ]),
      ]),
    ]);
    expect(paths(scan)).toEqual([
      'scans/top.pdf',
      'scans/2024/mid.png',
      'scans/2024/q1/deep.pdf',
    ]);
    expect(scan.folders).toBe(1);
  });

  it('keeps reading a folder until readEntries returns an empty batch', async () => {
    const children = Array.from({ length: 250 }, (_, i) =>
      fileEntry(`many/f${i}.pdf`)
    );
    const dir = dirEntry('many', children, 100);
    const entries = await readAllEntries(
      dir as unknown as Parameters<typeof readAllEntries>[0]
    );
    expect(entries).toHaveLength(250);
    // 100 + 100 + 50, then the empty batch that ends it.
    expect(dir.reads).toBe(4);
  });

  it('finds every file of a folder larger than one readEntries batch', async () => {
    const children = Array.from({ length: 120 }, (_, i) =>
      fileEntry(`many/f${i}.pdf`)
    );
    const scan = await drop([dirEntry('many', children, 100)]);
    expect(scan.files).toHaveLength(120);
  });

  it('stops calling readEntries once the file cap is reached', async () => {
    // 100 per batch: five batches fill the cap, the rest is never asked for.
    const children = Array.from({ length: MAX_FOLDER_FILES + 300 }, (_, i) =>
      fileEntry(`huge/f${i}.pdf`)
    );
    const dir = dirEntry('huge', children, 100);
    const scan = await drop([dir]);
    expect(scan.files).toHaveLength(MAX_FOLDER_FILES);
    expect(scan.truncated).toBe(true);
    expect(dir.reads).toBe(MAX_FOLDER_FILES / 100);
  });

  it('does not open further folders once the cap is reached', async () => {
    const first = dirEntry(
      'big/a',
      Array.from({ length: MAX_FOLDER_FILES }, (_, i) =>
        fileEntry(`big/a/f${i}.pdf`)
      )
    );
    const second = dirEntry('big/b', [fileEntry('big/b/late.pdf')]);
    const scan = await drop([dirEntry('big', [first, second])]);
    expect(scan.files).toHaveLength(MAX_FOLDER_FILES);
    expect(scan.truncated).toBe(true);
    expect(second.reads).toBe(0);
  });

  it('handles an empty folder without error', async () => {
    const scan = await drop([dirEntry('empty', [])]);
    expect(scan.files).toEqual([]);
    expect(scan.folders).toBe(1);
    expect(scan.truncated).toBe(false);
  });

  it('keeps mixed file types and skips hidden ones and hidden folders', async () => {
    const scan = await drop([
      dirEntry('mixed', [
        fileEntry('mixed/a.pdf'),
        fileEntry('mixed/b.docx'),
        fileEntry('mixed/c.jpg'),
        fileEntry('mixed/.DS_Store'),
        dirEntry('mixed/.git', [fileEntry('mixed/.git/HEAD')]),
      ]),
    ]);
    expect(paths(scan)).toEqual(['mixed/a.pdf', 'mixed/b.docx', 'mixed/c.jpg']);
    expect(scan.skipped).toBe(2);
  });

  it('counts a folder it cannot read instead of failing the whole drop', async () => {
    const broken: FakeEntry = {
      isFile: false,
      isDirectory: true,
      name: 'locked',
      fullPath: '/locked',
      createReader: () => ({
        readEntries: (_ok, fail) => fail?.(new Error('denied')),
      }),
    };
    const scan = await drop([broken, fileEntry('a.pdf')]);
    expect(paths(scan)).toEqual(['a.pdf']);
    expect(scan.unreadable).toBe(1);
  });

  it('falls back to the plain file list without webkitGetAsEntry', async () => {
    const file = new File(['x'], 'a.pdf');
    const scan = await scanDataTransfer({
      files: [file] as unknown as FileList,
      items: [{ kind: 'file', type: '' }] as unknown as DataTransferItemList,
    });
    expect(paths(scan)).toEqual(['a.pdf']);
    expect(scan.folders).toBe(0);
  });

  it('falls back to the plain file list when there are no items', async () => {
    const file = new File(['x'], 'a.pdf');
    const scan = await scanDataTransfer({
      files: [file] as unknown as FileList,
    });
    expect(paths(scan)).toEqual(['a.pdf']);
  });
});

describe('helpers', () => {
  it('recognises hidden and system files', () => {
    expect(isIgnoredPath('a/.DS_Store')).toBe(true);
    expect(isIgnoredPath('a/._scan.pdf')).toBe(true);
    expect(isIgnoredPath('a/THUMBS.DB')).toBe(true);
    expect(isIgnoredPath('a/desktop.ini')).toBe(true);
    expect(isIgnoredPath('.hidden/scan.pdf')).toBe(true);
    expect(isIgnoredPath('a/scan.pdf')).toBe(false);
  });

  it('detects the folder picker only where the browser has it', () => {
    expect(supportsFolderPicker()).toBe(false); // jsdom
    setFolderPickerSupport(true);
    expect(supportsFolderPicker()).toBe(true);
  });
});
