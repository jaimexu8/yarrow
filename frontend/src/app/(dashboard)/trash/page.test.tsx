import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import type { DocumentSummary } from '@/lib/documents';
import TrashPage from './page';

vi.mock('@/lib/documents', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/documents')>();
  return {
    ...actual,
    listTrash: vi.fn(),
    restoreDocument: vi.fn(),
    permanentlyDeleteDocument: vi.fn(),
    emptyTrash: vi.fn(),
  };
});

import {
  emptyTrash,
  listTrash,
  permanentlyDeleteDocument,
  restoreDocument,
} from '@/lib/documents';

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (
    this: HTMLDialogElement
  ) {
    this.open = true;
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.open = false;
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeDoc(
  id: string,
  filename: string,
  deletedAt: string
): DocumentSummary {
  return {
    id,
    filename,
    file_size_bytes: 1000,
    file_type: 'application/pdf',
    page_count: 1,
    status: 'completed',
    error_message: null,
    created_at: '2026-01-01T00:00:00',
    deleted_at: deletedAt,
    folder_id: null,
  };
}

it('shows an empty state when trash has no documents', async () => {
  vi.mocked(listTrash).mockResolvedValue([]);
  render(<TrashPage />);
  await screen.findByText('Trash is empty');
  expect(screen.getByText(/show up here for 7 days/)).toBeTruthy();
});

it('restores a document and removes it from the list', async () => {
  const doc = makeDoc('a', 'notes.pdf', new Date().toISOString());
  vi.mocked(listTrash).mockResolvedValue([doc]);
  vi.mocked(restoreDocument).mockResolvedValue({ ...doc, deleted_at: null });
  render(<TrashPage />);
  await screen.findByText('notes.pdf');
  fireEvent.click(screen.getByRole('button', { name: /Restore/ }));
  await waitFor(() => expect(restoreDocument).toHaveBeenCalledWith('a'));
  await waitFor(() => expect(screen.queryByText('notes.pdf')).toBeNull());
});

it('permanently deletes a document after confirm', async () => {
  const doc = makeDoc('a', 'notes.pdf', new Date().toISOString());
  vi.mocked(listTrash).mockResolvedValue([doc]);
  vi.mocked(permanentlyDeleteDocument).mockResolvedValue();
  render(<TrashPage />);
  await screen.findByText('notes.pdf');
  fireEvent.click(screen.getByRole('button', { name: /Delete forever/ }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByText(/permanently deleted/)).toBeTruthy();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Delete forever' })
  );
  await waitFor(() =>
    expect(permanentlyDeleteDocument).toHaveBeenCalledWith('a')
  );
  await waitFor(() => expect(screen.queryByText('notes.pdf')).toBeNull());
});

it('empties trash after confirm', async () => {
  const doc = makeDoc('a', 'notes.pdf', new Date().toISOString());
  vi.mocked(listTrash).mockResolvedValue([doc]);
  vi.mocked(emptyTrash).mockResolvedValue();
  render(<TrashPage />);
  await screen.findByText('notes.pdf');
  fireEvent.click(screen.getByRole('button', { name: /Empty trash/ }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByText(/permanently deleted/)).toBeTruthy();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Empty trash' }));
  await waitFor(() => expect(emptyTrash).toHaveBeenCalled());
  await screen.findByText('Trash is empty');
});
