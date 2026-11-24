import { afterEach, beforeAll, beforeEach, expect, test, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import type { DocumentSummary } from '@/lib/documents';
import { ReprocessButton } from './ReprocessBar';

// Exercise the real reprocessDocument -> api.post chain so the request body
// can be checked, the way other viewer tests mock the API module.
const { post, viewer } = vi.hoisted(() => ({
  post: vi.fn(),
  viewer: { document: null as unknown as DocumentSummary },
}));

vi.mock('@/lib/api', () => ({
  default: { post, get: vi.fn() },
}));

vi.mock('./ViewerContext', () => ({
  useViewer: () => viewer,
}));

function finishedDoc(): DocumentSummary {
  return {
    id: 'doc-1',
    filename: 'scan.pdf',
    file_size_bytes: 1024,
    file_type: 'application/pdf',
    page_count: 2,
    status: 'completed',
    error_message: null,
    error_stage: null,
    created_at: '2026-11-01T09:00:00',
    folder_id: null,
    reprocess: {
      scope: 'all',
      interrupted: false,
      pages: 2,
      page_numbers: [],
    },
  };
}

/** The single Modal that is currently open (closed ones have no aria-modal). */
function openDialog(): HTMLElement {
  const dialog = screen
    .getAllByRole('dialog')
    .find((node) => node.getAttribute('aria-modal') === 'true');
  if (!dialog) throw new Error('No open confirm dialog');
  return dialog;
}

beforeAll(() => {
  // jsdom does not implement the native <dialog> used by Modal.
  HTMLDialogElement.prototype.showModal = vi.fn(function (
    this: HTMLDialogElement
  ) {
    this.open = true;
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.open = false;
  });
});

beforeEach(() => {
  viewer.document = finishedDoc();
  post.mockResolvedValue({ data: finishedDoc() });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test('a finished document shows both Reprocess and Reprocess with ML', () => {
  render(<ReprocessButton onReprocessed={() => {}} />);
  expect(screen.getByRole('button', { name: 'Reprocess' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Reprocess with ML' })).toBeTruthy();
});

test('clicking Reprocess with ML posts skip_text_layer true', async () => {
  render(<ReprocessButton onReprocessed={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Reprocess with ML' }));
  fireEvent.click(
    within(openDialog()).getByRole('button', { name: 'Reprocess with ML' })
  );
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith('/api/v1/documents/doc-1/reprocess', {
      skip_text_layer: true,
    })
  );
});

test('clicking the ordinary Reprocess still posts no body', async () => {
  render(<ReprocessButton onReprocessed={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Reprocess' }));
  fireEvent.click(within(openDialog()).getByRole('button', { name: 'Reprocess' }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith('/api/v1/documents/doc-1/reprocess')
  );
  expect(post).toHaveBeenCalledTimes(1);
});

test('the confirmation dialog flow works for both buttons', async () => {
  const onReprocessed = vi.fn();
  render(<ReprocessButton onReprocessed={onReprocessed} />);

  // The ordinary button asks first; cancelling sends nothing.
  fireEvent.click(screen.getByRole('button', { name: 'Reprocess' }));
  expect(
    within(openDialog()).getByRole('button', { name: 'Cancel' })
  ).toBeTruthy();
  fireEvent.click(within(openDialog()).getByRole('button', { name: 'Cancel' }));
  expect(post).not.toHaveBeenCalled();

  // The ML button confirms through its own dialog and reports the change.
  fireEvent.click(screen.getByRole('button', { name: 'Reprocess with ML' }));
  expect(
    within(openDialog()).getByRole('button', { name: 'Reprocess with ML' })
  ).toBeTruthy();
  fireEvent.click(
    within(openDialog()).getByRole('button', { name: 'Reprocess with ML' })
  );
  await waitFor(() => expect(onReprocessed).toHaveBeenCalledTimes(1));
});
