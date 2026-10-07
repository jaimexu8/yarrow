import { isAxiosError } from 'axios';
import api from './api';

export type ExportFormat = 'markdown' | 'text';

const EXTENSION: Record<ExportFormat, string> = {
  markdown: 'md',
  text: 'txt',
};

/**
 * The filename the browser should save the file under. The server sends both
 * an ASCII-safe form and a UTF-8 percent-encoded form; the latter wins when
 * present, since it is the one that can carry non-ASCII characters.
 */
function filenameFromDisposition(
  header: string | undefined,
  fallback: string
): string {
  if (!header) return fallback;
  const unicode = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (unicode) {
    try {
      return decodeURIComponent(unicode[1]);
    } catch {
      // A malformed encoding is treated as absent
    }
  }
  const plain = /filename="([^"]*)"/i.exec(header);
  if (plain) return plain[1];
  return fallback;
}

function stemOf(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot > 0 ? filename.slice(0, dot) : filename;
}

/**
 * The one thing a Blob download needs that jsdom and a plain test cannot give:
 * the browser actually moving the bytes to disk. Kept separate so a test can
 * stub exactly this.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * Errors come back as a Blob because of the responseType, so a failure would
 * show a generic message instead of the server's reason. When the error body
 * is JSON, parse it into the axios error so toApiError can read it.
 */
async function surfaceJsonError(error: unknown): Promise<void> {
  if (!isAxiosError(error) || !error.response) return;
  const data = error.response.data;
  if (data instanceof Blob && data.type.includes('application/json')) {
    try {
      error.response.data = JSON.parse(await data.text());
    } catch {
      // Not JSON after all; toApiError falls back to a generic message
    }
  }
}

/**
 * Download the document's extracted content as Markdown or plain text.
 * Fetched through the API client rather than pointed at by an <a>, because the
 * endpoint needs the bearer token, which only the client sends.
 */
export async function exportDocument(
  documentId: string,
  format: ExportFormat,
  filename: string
): Promise<void> {
  try {
    const res = await api.get<Blob>(`/api/v1/documents/${documentId}/export`, {
      params: { format },
      responseType: 'blob',
    });
    saveBlob(
      res.data,
      filenameFromDisposition(
        res.headers['content-disposition'],
        `${stemOf(filename)}.${EXTENSION[format]}`
      )
    );
  } catch (error) {
    await surfaceJsonError(error);
    throw error;
  }
}
