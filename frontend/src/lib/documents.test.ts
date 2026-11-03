import { afterEach, describe, expect, it, vi } from 'vitest';
import api from './api';
import {
  TRASH_RETENTION_DAYS,
  listDocuments,
  moveDocumentToFolder,
  trashDaysLeft,
} from './documents';

vi.mock('./api', () => ({
  default: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}));

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

it('keeps a fixed 7-day trash window', () => {
  expect(TRASH_RETENTION_DAYS).toBe(7);
});

it('counts whole days left in trash from deleted_at', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-01-08T00:00:00Z'));
  expect(trashDaysLeft('2026-01-01T00:00:00')).toBe(0);
  expect(trashDaysLeft('2026-01-02T00:00:00')).toBe(1);
  expect(trashDaysLeft('2026-01-07T00:00:00')).toBe(6);
});

describe('documents client folders', () => {
  it('listDocuments omits folder_id for the full library', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: [] });
    await listDocuments();
    expect(api.get).toHaveBeenCalledWith('/api/v1/documents/', {
      params: undefined,
    });
  });

  it('listDocuments sends null as the string null for unfiled', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: [] });
    await listDocuments(null);
    expect(api.get).toHaveBeenCalledWith('/api/v1/documents/', {
      params: { folder_id: 'null' },
    });
  });

  it('listDocuments sends a folder id', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: [] });
    await listDocuments('folder-1');
    expect(api.get).toHaveBeenCalledWith('/api/v1/documents/', {
      params: { folder_id: 'folder-1' },
    });
  });

  it('moveDocumentToFolder patches folder_id', async () => {
    const doc = { id: 'doc-1', folder_id: 'folder-1' };
    vi.mocked(api.patch).mockResolvedValueOnce({ data: doc });
    const result = await moveDocumentToFolder('doc-1', 'folder-1');
    expect(result).toEqual(doc);
    expect(api.patch).toHaveBeenCalledWith('/api/v1/documents/doc-1/folder', {
      folder_id: 'folder-1',
    });
  });

  it('moveDocumentToFolder can unfile with null', async () => {
    const doc = { id: 'doc-1', folder_id: null };
    vi.mocked(api.patch).mockResolvedValueOnce({ data: doc });
    await moveDocumentToFolder('doc-1', null);
    expect(api.patch).toHaveBeenCalledWith('/api/v1/documents/doc-1/folder', {
      folder_id: null,
    });
  });
});
