/**
 * Finding the files inside a chosen or dropped folder (US-81).
 *
 * Two ways in:
 * - the folder picker (`<input webkitdirectory>`), which hands over every
 *   file below the folder at once, each with a `webkitRelativePath`;
 * - drag and drop, where a dropped folder arrives as a directory entry that
 *   has to be walked level by level (`webkitGetAsEntry` / `readEntries`).
 *
 * Both end in the same list of files with their path inside the folder, so
 * the Upload page can show where each file came from and tell apart two
 * files with the same name in different subfolders. The files are then
 * checked and uploaded exactly like files chosen one by one.
 */

/** At most this many files are taken from one selection or drop. */
export const MAX_FOLDER_FILES = 500;

/** Operating-system bookkeeping files that are never documents. */
const SYSTEM_FILE_NAMES = new Set(['thumbs.db', 'desktop.ini', 'ehthumbs.db']);

export type FoundFile = {
  file: File;
  /** Where the file sits, e.g. "scans/2024/receipt.pdf" (or just its name). */
  path: string;
};

export type FolderScan = {
  files: FoundFile[];
  /** How many folders the selection contained (0 for loose files). */
  folders: number;
  /** Hidden and system files that were left out (.DS_Store, Thumbs.db, …). */
  skipped: number;
  /** Files or folders the browser could not read. */
  unreadable: number;
  /** True when the selection held more than MAX_FOLDER_FILES files. */
  truncated: boolean;
};

/**
 * True for hidden and system files that should not be offered for upload:
 * anything inside a hidden folder or named with a leading dot (.DS_Store,
 * ._resource forks, .git/…), plus Thumbs.db and desktop.ini.
 */
export function isIgnoredPath(path: string): boolean {
  const parts = path.split('/').filter(Boolean);
  const name = parts[parts.length - 1] ?? '';
  if (SYSTEM_FILE_NAMES.has(name.toLowerCase())) return true;
  return parts.some((part) => part.startsWith('.'));
}

/** Whether this browser can open a folder picker. */
export function supportsFolderPicker(): boolean {
  if (typeof document === 'undefined') return false;
  return 'webkitdirectory' in document.createElement('input');
}

function emptyScan(): FolderScan {
  return { files: [], folders: 0, skipped: 0, unreadable: 0, truncated: false };
}

/** Adds one file to the scan, unless it is ignored or the cap is reached. */
function take(scan: FolderScan, file: File, path: string) {
  if (isIgnoredPath(path)) {
    scan.skipped += 1;
  } else if (scan.files.length >= MAX_FOLDER_FILES) {
    scan.truncated = true;
  } else {
    scan.files.push({ file, path });
  }
}

/**
 * Files from an `<input type="file">`, ordinary or `webkitdirectory`. A
 * folder picker sets `webkitRelativePath` ("folder/sub/file.pdf"); ordinary
 * picks leave it empty and keep their plain name.
 */
export function scanFileList(files: FileList | File[]): FolderScan {
  const scan = emptyScan();
  const topFolders = new Set<string>();
  for (const file of Array.from(files)) {
    const relative = file.webkitRelativePath || '';
    if (relative.includes('/')) topFolders.add(relative.split('/')[0]);
    take(scan, file, relative || file.name);
  }
  scan.folders = topFolders.size;
  return scan;
}

// The parts of the File and Directory Entries API used here. Declared
// locally so tests can hand in plain objects.
type Entry = {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  fullPath: string;
};
type FileEntry = Entry & {
  file(success: (file: File) => void, failure?: (error: unknown) => void): void;
};
type DirectoryReader = {
  readEntries(
    success: (entries: Entry[]) => void,
    failure?: (error: unknown) => void
  ): void;
};
type DirectoryEntry = Entry & { createReader(): DirectoryReader };

function readFile(entry: FileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

function readBatch(reader: DirectoryReader): Promise<Entry[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

/**
 * Every entry directly inside a folder. `readEntries` returns them in
 * batches (Chrome gives at most 100 at a time), so keep asking until it
 * returns an empty batch.
 */
export async function readAllEntries(dir: DirectoryEntry): Promise<Entry[]> {
  const reader = dir.createReader();
  const all: Entry[] = [];
  for (;;) {
    const batch = await readBatch(reader);
    if (batch.length === 0) return all;
    all.push(...batch);
  }
}

/** True once the cap is reached; anything still unread is left out. */
function full(scan: FolderScan): boolean {
  if (scan.files.length < MAX_FOLDER_FILES) return false;
  // There may be more we haven't looked at, so say the list was cut short.
  // (At exactly the cap this can be said when nothing was left; the
  // message is still true: those are the first MAX_FOLDER_FILES files.)
  scan.truncated = true;
  return true;
}

async function walk(entry: Entry, scan: FolderScan): Promise<void> {
  const path = entry.fullPath.replace(/^\/+/, '') || entry.name;
  if (scan.truncated || full(scan)) return;
  if (entry.isDirectory) {
    // Hidden folders (.git, .Trash, …) are skipped whole.
    if (isIgnoredPath(path)) {
      scan.skipped += 1;
      return;
    }
    // Batch by batch, so a huge folder stops being read once the cap is
    // reached instead of being listed in full first.
    const reader = (entry as DirectoryEntry).createReader();
    for (;;) {
      if (full(scan)) return;
      let batch: Entry[];
      try {
        batch = await readBatch(reader);
      } catch {
        scan.unreadable += 1;
        return;
      }
      if (batch.length === 0) return;
      for (const child of batch) {
        await walk(child, scan);
        if (scan.truncated) return;
      }
    }
  } else if (entry.isFile) {
    if (isIgnoredPath(path)) {
      scan.skipped += 1;
      return;
    }
    try {
      take(scan, await readFile(entry as FileEntry), path);
    } catch {
      scan.unreadable += 1;
    }
  }
}

/**
 * Files from a drop, walking into any dropped folders.
 *
 * Must be called while the drop event is being handled: the browser only
 * lets the drop's items be read during the event, so the entries (or, in
 * browsers without `webkitGetAsEntry`, the plain file list) are copied out
 * before anything waits.
 */
export function scanDataTransfer(
  dataTransfer: Pick<DataTransfer, 'files'> & {
    items?: DataTransfer['items'] | ArrayLike<DataTransferItem>;
  }
): Promise<FolderScan> {
  const items = dataTransfer.items ? Array.from(dataTransfer.items) : [];
  const entries: Entry[] = [];
  let entriesAvailable = items.length > 0;
  for (const item of items) {
    if (item.kind !== 'file') continue;
    if (typeof item.webkitGetAsEntry !== 'function') {
      entriesAvailable = false;
      break;
    }
    const entry = item.webkitGetAsEntry() as Entry | null;
    if (entry) entries.push(entry);
  }
  if (!entriesAvailable || entries.length === 0) {
    return Promise.resolve(scanFileList(Array.from(dataTransfer.files)));
  }
  return (async () => {
    const scan = emptyScan();
    for (const entry of entries) {
      if (entry.isDirectory) scan.folders += 1;
      await walk(entry, scan);
    }
    return scan;
  })();
}
