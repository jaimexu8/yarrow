import { afterEach, beforeAll, expect, test, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import type { DocumentStatus, DocumentSummary } from '@/lib/documents';
import { bulkDelete, bulkReprocess } from '@/lib/documents';
import { DocumentLibrary } from './DocumentTable';

const state = vi.hoisted(() => ({
  documents: [] as DocumentSummary[],
  remove: vi.fn(),
}));

vi.mock('@/lib/useDocuments', () => ({
  useDocuments: () => ({
    documents: state.documents,
    error: null,
    replace: vi.fn(),
    remove: state.remove,
    reload: vi.fn(),
  }),
}));

vi.mock('@/lib/documents', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/documents')>();
  return {
    ...actual,
    bulkDelete: vi.fn(),
    bulkReprocess: vi.fn(),
  };
});

function checkbox(filename: string) {
  return screen.getByRole('checkbox', { name: `Select ${filename}` });
}

beforeAll(() => {
  // The confirm dialog is a native <dialog>, which jsdom does not implement.
  HTMLDialogElement.prototype.showModal = vi.fn(function (
    this: HTMLDialogElement
  ) {
    this.open = true;
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.open = false;
  });
});

function makeDoc(
  id: string,
  filename: string,
  status: DocumentStatus | null
): DocumentSummary {
  return {
    id,
    filename,
    file_size_bytes: 1000,
    file_type: 'application/pdf',
    page_count: 1,
    status,
    error_message: null,
    created_at: '2025-01-01T00:00:00',
  };
}

const DOCS: DocumentSummary[] = [
  makeDoc('a', 'report-a.pdf', 'completed'),
  makeDoc('b', 'report-b.pdf', 'completed'),
  makeDoc('c', 'invoice-c.pdf', 'failed'),
  makeDoc('d', 'notes-d.pdf', 'queued'),
  makeDoc('e', 'draft-e.pdf', null),
];

function selectStatus(value: string) {
  fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), {
    target: { value },
  });
}

function nameLink(filename: string) {
  return screen.queryByRole('link', { name: filename });
}

afterEach(() => {
  state.documents = [];
  cleanup();
  vi.clearAllMocks();
});

test('selecting rows shows a bulk action bar with the count', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  fireEvent.click(checkbox('report-a.pdf'));
  fireEvent.click(checkbox('report-b.pdf'));
  expect(screen.getByText('2 documents selected')).toBeTruthy();
  // The bulk bar offers both actions and a way to clear.
  expect(screen.getByRole('button', { name: /Reprocess/ })).toBeTruthy();
  expect(screen.getByRole('button', { name: /Delete/ })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Clear selection' })).toBeTruthy();
});

test('clearing the selection hides the bulk bar', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  fireEvent.click(checkbox('report-a.pdf'));
  expect(screen.getByText('1 document selected')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Clear selection' }));
  expect(screen.queryByText('1 document selected')).toBeNull();
});

test('the select-all header checkbox selects every visible row', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  fireEvent.click(
    screen.getByRole('checkbox', { name: 'Select all documents' })
  );
  expect(screen.getByText('5 documents selected')).toBeTruthy();
  expect(checkbox('report-a.pdf').getAttribute('aria-checked')).toBe('true');
});

test('a shift+click selects a range of rows', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  fireEvent.click(checkbox('report-a.pdf'));
  fireEvent.click(checkbox('invoice-c.pdf'), { shiftKey: true });
  expect(screen.getByText('3 documents selected')).toBeTruthy();
  expect(checkbox('report-b.pdf').getAttribute('aria-checked')).toBe('true');
});

test('confirming a bulk delete calls the api and drops the rows', async () => {
  state.documents = DOCS;
  vi.mocked(bulkDelete).mockResolvedValue([
    { document_id: 'a', ok: true, message: null },
    { document_id: 'b', ok: true, message: null },
  ]);
  render(<DocumentLibrary />);
  fireEvent.click(checkbox('report-a.pdf'));
  fireEvent.click(checkbox('report-b.pdf'));
  fireEvent.click(screen.getByRole('button', { name: /Delete/ }));

  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

  await vi.waitFor(() => expect(bulkDelete).toHaveBeenCalledWith(['a', 'b']));
  await vi.waitFor(() => expect(state.remove).toHaveBeenCalledWith('a'));
  expect(state.remove).toHaveBeenCalledWith('b');
});

test('a failed bulk reprocess keeps the selection and shows the error', async () => {
  state.documents = DOCS;
  vi.mocked(bulkReprocess).mockRejectedValue(new Error('boom'));
  render(<DocumentLibrary />);
  fireEvent.click(checkbox('report-a.pdf'));
  fireEvent.click(screen.getByRole('button', { name: /Reprocess/ }));

  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Reprocess' }));

  await vi.waitFor(() => expect(bulkReprocess).toHaveBeenCalledWith(['a']));
  // The error is shown inside the dialog and the selection is kept.
  await screen.findByText(/went wrong|reach the server/i);
  expect(screen.getByText('1 document selected')).toBeTruthy();
});

test('shows every document when the status filter is Any', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  expect(screen.getByText('5 documents')).toBeTruthy();
  for (const doc of DOCS) {
    expect(nameLink(doc.filename), doc.filename).not.toBeNull();
  }
});

test('filters to completed documents only', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  selectStatus('completed');
  expect(screen.getByText('2 of 5 documents')).toBeTruthy();
  expect(nameLink('report-a.pdf')).not.toBeNull();
  expect(nameLink('report-b.pdf')).not.toBeNull();
  expect(nameLink('invoice-c.pdf')).toBeNull();
  expect(nameLink('notes-d.pdf')).toBeNull();
  // A document with no status is not completed, so it stays hidden.
  expect(nameLink('draft-e.pdf')).toBeNull();
});

test('filters to failed documents only', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  selectStatus('failed');
  expect(screen.getByText('1 of 5 documents')).toBeTruthy();
  expect(nameLink('invoice-c.pdf')).not.toBeNull();
  expect(nameLink('report-a.pdf')).toBeNull();
  expect(nameLink('report-b.pdf')).toBeNull();
  expect(nameLink('notes-d.pdf')).toBeNull();
  expect(nameLink('draft-e.pdf')).toBeNull();
});

test('resetting to Any status shows all documents again', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  selectStatus('completed');
  expect(screen.getByText('2 of 5 documents')).toBeTruthy();
  selectStatus('');
  expect(screen.getByText('5 documents')).toBeTruthy();
  for (const doc of DOCS) {
    expect(nameLink(doc.filename), doc.filename).not.toBeNull();
  }
});

test('combines the search box and the status filter', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  selectStatus('completed');
  fireEvent.change(screen.getByRole('searchbox'), {
    target: { value: 'report' },
  });
  expect(screen.getByText('2 of 5 documents')).toBeTruthy();
  expect(nameLink('report-a.pdf')).not.toBeNull();
  expect(nameLink('report-b.pdf')).not.toBeNull();
  expect(nameLink('invoice-c.pdf')).toBeNull();
});

test('shows a clear-filters empty state when a status matches nothing', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  selectStatus('processing');
  expect(screen.getByText('Clear filters')).toBeTruthy();
  fireEvent.click(screen.getByText('Clear filters'));
  expect(screen.getByText('5 documents')).toBeTruthy();
  for (const doc of DOCS) {
    expect(nameLink(doc.filename), doc.filename).not.toBeNull();
  }
});
