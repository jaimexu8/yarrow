import { afterEach, expect, it, vi } from 'vitest';
import api from './api';
import { listDocuments, type DocumentSummary } from './documents';
import {
  addDocumentTag,
  documentHasTag,
  listTags,
  normalizeTagName,
  removeDocumentTag,
  tagNameError,
  uniqueTags,
} from './tags';

vi.mock('./api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

afterEach(() => {
  vi.clearAllMocks();
});

function doc(
  id: string,
  tags: { id: string; name: string }[] = []
): DocumentSummary {
  return {
    id,
    filename: `${id}.pdf`,
    file_size_bytes: 1000,
    file_type: 'application/pdf',
    page_count: 1,
    status: 'completed',
    error_message: null,
    created_at: null,
    folder_id: null,
    tags,
  };
}

it('trims tag names', () => {
  expect(normalizeTagName('  Invoice  ')).toBe('Invoice');
});

it('rejects empty and too-long tag names', () => {
  expect(tagNameError('   ')).toBe('Tag name cannot be empty.');
  expect(tagNameError('x'.repeat(41))).toMatch(/40 characters/);
  expect(tagNameError('Invoice')).toBeNull();
});

it('lists unique tags from documents, sorted by name', () => {
  const tags = uniqueTags([
    doc('a', [
      { id: '2', name: 'tax' },
      { id: '1', name: 'invoice' },
    ]),
    doc('b', [{ id: '1', name: 'invoice' }]),
    doc('c'),
  ]);
  expect(tags.map((tag) => tag.name)).toEqual(['invoice', 'tax']);
});

it('documentHasTag is true only for that tag', () => {
  const tagged = doc('a', [{ id: '1', name: 'invoice' }]);
  expect(documentHasTag(tagged, '1')).toBe(true);
  expect(documentHasTag(tagged, '2')).toBe(false);
  expect(documentHasTag(doc('b'), '1')).toBe(false);
});

it('listTags fetches this user tags', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: [] });
  await listTags();
  expect(api.get).toHaveBeenCalledWith('/api/v1/tags/');
});

it('listDocuments can filter by tag_id', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: [] });
  await listDocuments('tag-1');
  expect(api.get).toHaveBeenCalledWith('/api/v1/documents/', {
    params: { tag_id: 'tag-1' },
  });
});

it('addDocumentTag posts the name', async () => {
  const updated = doc('doc-1', [{ id: 'tag-1', name: 'invoice' }]);
  vi.mocked(api.post).mockResolvedValue({ data: updated });
  const res = await addDocumentTag('doc-1', 'invoice');
  expect(res).toEqual(updated);
  expect(api.post).toHaveBeenCalledWith('/api/v1/documents/doc-1/tags', {
    name: 'invoice',
  });
});

it('removeDocumentTag deletes the association', async () => {
  vi.mocked(api.delete).mockResolvedValue({ data: null });
  await removeDocumentTag('doc-1', 'tag-1');
  expect(api.delete).toHaveBeenCalledWith('/api/v1/documents/doc-1/tags/tag-1');
});
