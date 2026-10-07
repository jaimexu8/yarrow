import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { isAxiosError } from 'axios';
import api from './api';
import { toApiError } from './errors';
import { exportDocument } from './export';

vi.mock('./api', () => ({
  default: { get: vi.fn() },
}));

function jsonResponse(body: string, disposition: string | null) {
  return {
    data: new Blob([body], { type: 'text/markdown; charset=utf-8' }),
    headers: disposition ? { 'content-disposition': disposition } : {},
  };
}

// Records the download name of the anchor the helper clicks.
function captureClickedName() {
  const names: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement
  ) {
    names.push(this.download);
  });
  return () => {
    expect(names).toHaveLength(1);
    return names[0];
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom does not implement object URLs
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => 'blob:test-url'),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

test('downloads the export with the filename from the server', async () => {
  const name = captureClickedName();
  vi.mocked(api.get).mockResolvedValueOnce(
    jsonResponse('# Title\n', 'attachment; filename="report.md"')
  );

  await exportDocument('doc-1', 'markdown', 'report.pdf');

  expect(api.get).toHaveBeenCalledWith('/api/v1/documents/doc-1/export', {
    params: { format: 'markdown' },
    responseType: 'blob',
  });
  expect(name()).toBe('report.md');
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-url');
});

test('prefers the UTF-8 filename form over the ASCII one', async () => {
  const name = captureClickedName();
  vi.mocked(api.get).mockResolvedValueOnce(
    jsonResponse(
      'text',
      'attachment; filename="berik-reports.md"; filename*=UTF-8\'\'Ber%C3%ADk%20reports.md'
    )
  );

  await exportDocument('doc-1', 'markdown', 'whatever.pdf');

  expect(name()).toBe('Berík reports.md');
});

test('falls back to the plain filename form', async () => {
  const name = captureClickedName();
  vi.mocked(api.get).mockResolvedValueOnce(
    jsonResponse('text', 'attachment; filename="report.md"')
  );

  await exportDocument('doc-1', 'markdown', 'report.pdf');

  expect(name()).toBe('report.md');
});

test('falls back to the document name when no header is sent', async () => {
  const name = captureClickedName();
  vi.mocked(api.get).mockResolvedValueOnce(jsonResponse('text', null));

  await exportDocument('doc-1', 'text', 'report.pdf');

  expect(name()).toBe('report.txt');
});

test('parses a JSON error body so the message reaches the UI', async () => {
  const error = Object.assign(new Error('Request failed with status 409'), {
    isAxiosError: true,
    response: {
      status: 409,
      data: new Blob(
        [
          JSON.stringify({
            detail: {
              detail: 'Document has not finished processing',
              code: 'DOCUMENT_NOT_PROCESSED',
            },
          }),
        ],
        { type: 'application/json' }
      ),
    },
  });
  vi.mocked(api.get).mockRejectedValueOnce(error);

  const caught = await exportDocument('doc-1', 'markdown', 'report.pdf').catch(
    (err) => err
  );

  expect(isAxiosError(caught)).toBe(true);
  expect(toApiError(caught).message).toBe(
    'Document has not finished processing'
  );
  expect(toApiError(caught).code).toBe('DOCUMENT_NOT_PROCESSED');
});
