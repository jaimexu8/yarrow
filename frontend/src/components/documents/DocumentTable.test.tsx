import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { DocumentStatus, DocumentSummary } from '@/lib/documents';
import { DocumentLibrary } from './DocumentTable';

const state = vi.hoisted(() => ({ documents: [] as DocumentSummary[] }));

vi.mock('@/lib/useDocuments', () => ({
  useDocuments: () => ({
    documents: state.documents,
    error: null,
    replace: vi.fn(),
    remove: vi.fn(),
    reload: vi.fn(),
  }),
}));

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
