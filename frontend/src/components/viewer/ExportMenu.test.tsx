import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { DocumentSummary } from '@/lib/documents';
import { exportDocument } from '@/lib/export';
import { ExportMenu } from './ExportMenu';

const { viewer } = vi.hoisted(() => ({
  viewer: { document: null as unknown as DocumentSummary },
}));

vi.mock('./ViewerContext', () => ({
  useViewer: () => viewer,
}));

vi.mock('@/lib/export', () => ({
  exportDocument: vi.fn(),
}));

function documentWith(status: DocumentSummary['status']): DocumentSummary {
  return {
    id: 'doc-1',
    filename: 'report.pdf',
    file_size_bytes: 1000,
    file_type: 'application/pdf',
    page_count: 1,
    status,
    error_message: null,
    created_at: null,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test('is disabled until the document is completed', () => {
  const statuses: (DocumentSummary['status'] | null)[] = [
    'queued',
    'processing',
    'failed',
    'canceled',
    null,
  ];
  for (const status of statuses) {
    viewer.document = documentWith(status);
    const { unmount } = render(<ExportMenu />);
    const button = screen.getByRole('button', { name: /export/i });
    expect(button.hasAttribute('disabled'), `status ${status}`).toBe(true);
    unmount();
  }
});

test('is enabled for a completed document', () => {
  viewer.document = documentWith('completed');
  render(<ExportMenu />);
  expect(
    screen.getByRole('button', { name: /export/i }).hasAttribute('disabled')
  ).toBe(false);
});

test('offers markdown and plain text, and exports the chosen format', async () => {
  viewer.document = documentWith('completed');
  render(<ExportMenu />);

  fireEvent.click(screen.getByRole('button', { name: /export/i }));
  expect(
    screen
      .getByRole('menuitem', { name: 'Markdown (.md)' })
      .hasAttribute('disabled')
  ).toBe(false);
  expect(
    screen
      .getByRole('menuitem', { name: 'Plain text (.txt)' })
      .hasAttribute('disabled')
  ).toBe(false);

  fireEvent.click(screen.getByRole('menuitem', { name: 'Markdown (.md)' }));
  expect(exportDocument).toHaveBeenCalledWith(
    'doc-1',
    'markdown',
    'report.pdf'
  );

  vi.mocked(exportDocument).mockClear();
  // Let the download settle so the button is no longer in its loading state
  await new Promise((resolve) => setTimeout(resolve, 0));
  fireEvent.click(screen.getByRole('button', { name: /export/i }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Plain text (.txt)' }));
  expect(exportDocument).toHaveBeenCalledWith('doc-1', 'text', 'report.pdf');
});

test('shows the server message when the export fails', async () => {
  viewer.document = documentWith('completed');
  vi.mocked(exportDocument).mockRejectedValueOnce(
    Object.assign(new Error('Request failed with status 409'), {
      isAxiosError: true,
      response: {
        status: 409,
        data: {
          detail: {
            detail: 'Document has not finished processing',
            code: 'DOCUMENT_NOT_PROCESSED',
          },
        },
      },
    })
  );
  render(<ExportMenu />);

  fireEvent.click(screen.getByRole('button', { name: /export/i }));
  await fireEvent.click(
    screen.getByRole('menuitem', { name: 'Markdown (.md)' })
  );

  expect((await screen.findByRole('alert')).textContent).toContain(
    'Document has not finished processing'
  );
});
