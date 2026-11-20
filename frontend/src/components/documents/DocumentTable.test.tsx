import { afterEach, beforeAll, describe, expect, test, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import type { DocumentStatus, DocumentSummary } from '@/lib/documents';
import {
  bulkDelete,
  bulkReprocess,
  moveDocumentToFolder,
} from '@/lib/documents';
import { addDocumentTag, removeDocumentTag } from '@/lib/tags';
import { createFolder, listFolders } from '@/lib/folders';
import { setDocumentPassword } from '@/lib/viewer';
import { DocumentLibrary } from './DocumentTable';
import { FailureNote } from './documentActions';

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
    moveDocumentToFolder: vi.fn(),
  };
});

vi.mock('@/lib/tags', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/tags')>();
  return {
    ...actual,
    addDocumentTag: vi.fn(),
    removeDocumentTag: vi.fn(),
  };
});

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'me' } }),
}));

vi.mock('@/lib/viewer', () => ({
  setDocumentPassword: vi.fn(),
}));

vi.mock('@/lib/folders', () => ({
  listFolders: vi.fn(async () => []),
  createFolder: vi.fn(),
  renameFolder: vi.fn(),
  deleteFolder: vi.fn(),
}));

function checkbox(filename: string) {
  return screen.getByRole('checkbox', { name: `Select ${filename}` });
}

beforeAll(() => {
  // the confirm dialog is a native <dialog>, which jsdom does not implement.
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
  status: DocumentStatus | null,
  tags: DocumentSummary['tags'] = []
): DocumentSummary {
  return {
    id,
    filename,
    file_size_bytes: 1000,
    file_type: 'application/pdf',
    page_count: 1,
    status,
    error_message: null,
    error_stage: null,
    created_at: '2025-01-01T00:00:00',
    folder_id: null,
    tags,
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

function selectTag(value: string) {
  fireEvent.change(screen.getByRole('combobox', { name: 'Tag' }), {
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
  vi.mocked(listFolders).mockResolvedValue([]);
});

test('selecting rows shows a bulk action bar with the count', () => {
  state.documents = DOCS;
  render(<DocumentLibrary />);
  fireEvent.click(checkbox('report-a.pdf'));
  fireEvent.click(checkbox('report-b.pdf'));
  expect(screen.getByText('2 documents selected')).toBeTruthy();
  // the bulk bar offers both actions and a way to clear.
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
  expect(within(dialog).getByText(/moved to Trash/)).toBeTruthy();
  expect(within(dialog).getByText(/restore them for 7 days/)).toBeTruthy();
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
  // the error is shown inside the dialog and the selection is kept.
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
  // a document with no status is not completed, so it stays hidden.
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

const TAGGED_DOCS: DocumentSummary[] = [
  makeDoc('a', 'report-a.pdf', 'completed', [
    { id: 'invoice', name: 'invoice' },
  ]),
  makeDoc('b', 'report-b.pdf', 'completed', [{ id: 'tax', name: 'tax' }]),
  makeDoc('c', 'invoice-c.pdf', 'failed', [{ id: 'invoice', name: 'invoice' }]),
];

test('shows tag chips on each row', () => {
  state.documents = TAGGED_DOCS;
  render(<DocumentLibrary />);
  expect(
    screen.getAllByRole('button', { name: /Remove tag invoice/ })
  ).toHaveLength(2);
  expect(
    screen.getByRole('button', {
      name: 'Remove tag tax from report-b.pdf',
    })
  ).toBeTruthy();
});

test('filters to documents with the chosen tag', () => {
  state.documents = TAGGED_DOCS;
  render(<DocumentLibrary />);
  selectTag('invoice');
  expect(screen.getByText('2 of 3 documents')).toBeTruthy();
  expect(nameLink('report-a.pdf')).not.toBeNull();
  expect(nameLink('invoice-c.pdf')).not.toBeNull();
  expect(nameLink('report-b.pdf')).toBeNull();
});

test('resetting to All tags shows every document again', () => {
  state.documents = TAGGED_DOCS;
  render(<DocumentLibrary />);
  selectTag('tax');
  expect(screen.getByText('1 of 3 documents')).toBeTruthy();
  selectTag('');
  expect(screen.getByText('3 documents')).toBeTruthy();
  expect(nameLink('report-b.pdf')).not.toBeNull();
});

test('removing a tag chip calls the api', async () => {
  state.documents = TAGGED_DOCS;
  vi.mocked(removeDocumentTag).mockResolvedValue();
  render(<DocumentLibrary />);
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Remove tag tax from report-b.pdf',
    })
  );
  await vi.waitFor(() =>
    expect(removeDocumentTag).toHaveBeenCalledWith('b', 'tax')
  );
});

test('adding a tag from the action menu posts the name', async () => {
  state.documents = [makeDoc('a', 'report-a.pdf', 'completed')];
  vi.mocked(addDocumentTag).mockResolvedValue(
    makeDoc('a', 'report-a.pdf', 'completed', [{ id: 'new', name: 'draft' }])
  );
  render(<DocumentLibrary />);
  fireEvent.click(
    screen.getByRole('button', { name: 'More actions for report-a.pdf' })
  );
  fireEvent.click(screen.getByRole('menuitem', { name: 'Add tag' }));
  fireEvent.change(screen.getByLabelText('Tag name'), {
    target: { value: 'draft' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add tag' }));
  await vi.waitFor(() =>
    expect(addDocumentTag).toHaveBeenCalledWith('a', 'draft')
  );
});

test('creating a folder lists it in the library', async () => {
  state.documents = DOCS;
  vi.mocked(createFolder).mockResolvedValue({
    id: 'folder-1',
    name: 'Receipts',
    created_at: null,
  });
  render(<DocumentLibrary />);
  fireEvent.click(screen.getByRole('button', { name: 'New folder' }));
  fireEvent.change(screen.getByLabelText('Folder name'), {
    target: { value: 'Receipts' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Create' }));
  await waitFor(() => expect(createFolder).toHaveBeenCalledWith('Receipts'));
  expect(screen.getByRole('button', { name: 'Receipts' })).toBeTruthy();
});

test('opening a folder shows only documents in that folder', async () => {
  const filed = { ...makeDoc('a', 'filed.pdf', 'completed'), folder_id: 'f1' };
  const loose = makeDoc('b', 'loose.pdf', 'completed');
  state.documents = [filed, loose];
  vi.mocked(listFolders).mockResolvedValue([
    { id: 'f1', name: 'Receipts', created_at: null },
  ]);
  render(<DocumentLibrary />);
  await screen.findByRole('button', { name: 'Receipts' });
  fireEvent.click(screen.getByRole('button', { name: 'Receipts' }));
  expect(nameLink('filed.pdf')).not.toBeNull();
  expect(nameLink('loose.pdf')).toBeNull();
});

test('unfiled hides documents that were moved into a folder', async () => {
  const filed = { ...makeDoc('a', 'filed.pdf', 'completed'), folder_id: 'f1' };
  const loose = makeDoc('b', 'loose.pdf', 'completed');
  state.documents = [filed, loose];
  vi.mocked(listFolders).mockResolvedValue([
    { id: 'f1', name: 'Receipts', created_at: null },
  ]);
  render(<DocumentLibrary />);
  await screen.findByRole('button', { name: 'Receipts' });
  fireEvent.click(screen.getByRole('button', { name: 'Unfiled' }));
  expect(nameLink('loose.pdf')).not.toBeNull();
  expect(nameLink('filed.pdf')).toBeNull();
});

test('move to folder sends the selected folder id', async () => {
  state.documents = DOCS;
  vi.mocked(listFolders).mockResolvedValue([
    { id: 'f1', name: 'Receipts', created_at: null },
  ]);
  vi.mocked(moveDocumentToFolder).mockResolvedValue({
    ...DOCS[0],
    folder_id: 'f1',
  });
  render(<DocumentLibrary />);
  await screen.findByRole('button', { name: 'Receipts' });
  fireEvent.click(
    screen.getByRole('button', { name: 'More actions for report-a.pdf' })
  );
  fireEvent.click(screen.getByRole('menuitem', { name: 'Move to folder' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('radio', { name: 'Receipts' }));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Move' }));
  await waitFor(() =>
    expect(moveDocumentToFolder).toHaveBeenCalledWith('a', 'f1')
  );
});

function openMenu(filename: string) {
  fireEvent.click(
    screen.getByRole('button', { name: `More actions for ${filename}` })
  );
}

test('Set password is offered only to the owner of a PDF', () => {
  state.documents = [
    { ...makeDoc('a', 'mine.pdf', 'completed'), owner_id: 'me' },
    { ...makeDoc('b', 'shared.pdf', 'completed'), owner_id: 'someone-else' },
    {
      ...makeDoc('c', 'photo.png', 'completed'),
      owner_id: 'me',
      file_type: 'image/png',
    },
  ];
  render(<DocumentLibrary />);

  openMenu('mine.pdf');
  expect(screen.queryByRole('menuitem', { name: 'Set password' })).toBeTruthy();
  fireEvent.keyDown(document.body, { key: 'Escape' });
  cleanup();

  render(<DocumentLibrary />);
  openMenu('shared.pdf');
  expect(screen.queryByRole('menuitem', { name: 'Set password' })).toBeNull();
  cleanup();

  render(<DocumentLibrary />);
  openMenu('photo.png');
  expect(screen.queryByRole('menuitem', { name: 'Set password' })).toBeNull();
});

test('Set password keeps the dialog open when the password is wrong', async () => {
  state.documents = [
    { ...makeDoc('a', 'mine.pdf', 'completed'), owner_id: 'me' },
  ];
  vi.mocked(setDocumentPassword).mockRejectedValueOnce(
    Object.assign(new Error('bad'), {
      isAxiosError: true,
      response: { status: 400, data: { detail: 'Incorrect PDF password' } },
    })
  );
  render(<DocumentLibrary />);
  openMenu('mine.pdf');
  fireEvent.click(screen.getByRole('menuitem', { name: 'Set password' }));

  const dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('PDF password'), {
    target: { value: 'nope' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));

  await waitFor(() =>
    expect(setDocumentPassword).toHaveBeenCalledWith('a', 'nope', false)
  );
  expect(
    await within(dialog).findByText('Incorrect password. Try again.')
  ).toBeTruthy();
describe('FailureNote', () => {
  test('shows the stage phrase and reason for a failed document', () => {
    render(
      <FailureNote
        doc={{
          ...makeDoc('c', 'invoice-c.pdf', 'failed'),
          error_message: '2 of 2 page(s) failed: 1, 2',
          error_stage: 'parsing',
        }}
      >
        <button type="button">Reprocess</button>
      </FailureNote>
    );

    expect(
      screen.getByText(
        /Failed while reading pages: 2 of 2 page\(s\) failed: 1, 2/
      )
    ).toBeTruthy();
    expect(screen.queryByText(/Some pages could not be read/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Reprocess' })).toBeTruthy();
  });

  test('keeps the amber warning for a completed document with partial failures', () => {
    render(
      <FailureNote
        doc={{
          ...makeDoc('a', 'report-a.pdf', 'completed'),
          error_message: '2 of 2 page(s) failed: 1, 2',
          error_stage: 'parsing',
        }}
      />
    );

    expect(
      screen.getByText(
        /Some pages could not be read: 2 of 2 page\(s\) failed: 1, 2/
      )
    ).toBeTruthy();
    expect(screen.queryByText(/Failed while/)).toBeNull();
  });

  test('shows nothing for a completed document with no error message', () => {
    const { container } = render(
      <FailureNote doc={makeDoc('a', 'report-a.pdf', 'completed')} />
    );

    expect(container.textContent).toBe('');
  });
});
