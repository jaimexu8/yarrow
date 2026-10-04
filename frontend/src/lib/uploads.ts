/**
 * Client-side checks for files before they are uploaded (US-37).
 *
 * These only save a round trip and give an instant explanation. The backend
 * is the real gate: it decides a file's type from its contents, not its
 * name, so a renamed file is still rejected there.
 *
 * Keep in sync with ALLOWED_EXTENSIONS / ACCEPTED_TYPES_TEXT and
 * MAX_UPLOAD_BYTES in backend/app/api/v1/endpoints/documents.py and config.py.
 */

export const ACCEPTED_EXTENSIONS = ['pdf', 'png', 'jpg', 'jpeg', 'gif'];
export const ACCEPTED_TYPES_TEXT = 'PDF, PNG, JPEG and GIF';
/** For <input type="file" accept>: extensions plus MIME types. */
export const ACCEPT_ATTRIBUTE = [
  ...ACCEPTED_EXTENSIONS.map((ext) => `.${ext}`),
  'application/pdf',
  'image/png',
  'image/jpeg',
  'image/gif',
].join(',');
export const MAX_UPLOAD_BYTES = 256 * 1024 * 1024;

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** A reason the file can't be uploaded, or null if it looks fine. */
export function checkFile(file: File): string | null {
  const extension = file.name.includes('.')
    ? file.name.split('.').pop()!.toLowerCase()
    : '';
  if (!ACCEPTED_EXTENSIONS.includes(extension)) {
    return `Unsupported file type. Yarrow accepts ${ACCEPTED_TYPES_TEXT} files.`;
  }
  if (file.size === 0) return 'File is empty';
  if (file.size > MAX_UPLOAD_BYTES) {
    return `File is too large. The limit is ${formatBytes(MAX_UPLOAD_BYTES)} per file.`;
  }
  return null;
}
