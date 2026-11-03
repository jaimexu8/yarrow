/**
 * Test helpers for US-81: stand-ins for the browser's folder APIs, which
 * jsdom does not have. Only imported by tests.
 */

/** A file as the folder picker hands it over, with its path in the folder. */
export function fileAt(path: string, body = '%PDF-1.4', type = '') {
  const name = path.split('/').pop()!;
  const file = new File([body], name, { type, lastModified: 1 });
  Object.defineProperty(file, 'webkitRelativePath', { value: path });
  return file;
}

export type FakeEntry = {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  fullPath: string;
  file?: (ok: (file: File) => void, fail?: (error: unknown) => void) => void;
  createReader?: () => {
    readEntries: (
      ok: (entries: FakeEntry[]) => void,
      fail?: (error: unknown) => void
    ) => void;
  };
};

/** A dropped file, as `webkitGetAsEntry()` returns it. */
export function fileEntry(path: string, body = '%PDF-1.4'): FakeEntry {
  const name = path.split('/').pop()!;
  const file = new File([body], name, { lastModified: 1 });
  return {
    isFile: true,
    isDirectory: false,
    name,
    fullPath: `/${path}`,
    file: (ok) => ok(file),
  };
}

/**
 * A dropped folder. Its reader hands back the children `batchSize` at a
 * time, then an empty batch, like Chrome's (which gives 100 at a time).
 * `reads` counts the readEntries calls.
 */
export function dirEntry(
  path: string,
  children: FakeEntry[],
  batchSize = 100
): FakeEntry & { reads: number } {
  const dir = {
    isFile: false,
    isDirectory: true,
    name: path.split('/').pop()!,
    fullPath: `/${path}`,
    reads: 0,
    createReader: () => {
      let offset = 0;
      return {
        readEntries: (ok: (entries: FakeEntry[]) => void) => {
          dir.reads += 1;
          const batch = children.slice(offset, offset + batchSize);
          offset += batch.length;
          // Answer later, as the browser does.
          setTimeout(() => ok(batch), 0);
        },
      };
    },
  };
  return dir;
}

/** A drop's dataTransfer holding these entries. */
export function dropOf(entries: FakeEntry[], files: File[] = []) {
  return {
    files,
    items: entries.map((entry) => ({
      kind: 'file',
      type: '',
      webkitGetAsEntry: () => entry,
    })),
  };
}

/** Makes `'webkitdirectory' in input` true or false for the next render. */
export function setFolderPickerSupport(supported: boolean) {
  const proto = HTMLInputElement.prototype as unknown as Record<
    string,
    unknown
  >;
  if (supported) {
    Object.defineProperty(proto, 'webkitdirectory', {
      configurable: true,
      get: () => false,
    });
  } else {
    delete proto.webkitdirectory;
  }
}

/**
 * A dropped folder that isn't read until `release()` is called, to look at
 * the page while a folder is still being read.
 */
export function heldDirEntry(path: string, children: FakeEntry[]) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  const inner = dirEntry(path, children);
  const entry: FakeEntry = {
    ...inner,
    createReader: () => {
      const reader = inner.createReader!();
      return {
        readEntries: (ok, fail) => {
          gate.then(() => reader.readEntries(ok, fail));
        },
      };
    },
  };
  return { entry, release };
}
