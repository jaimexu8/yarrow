import { afterEach, describe, expect, it, vi } from 'vitest';
import api from './api';
import {
  createFolder,
  deleteFolder,
  listFolders,
  renameFolder,
} from './folders';

vi.mock('./api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

afterEach(() => {
  vi.clearAllMocks();
});

const FOLDER = {
  id: 'folder-1',
  name: 'Invoices',
  created_at: '2026-01-01T00:00:00',
};

describe('folders client', () => {
  it('listFolders fetches the caller folders', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: [FOLDER] });
    const folders = await listFolders();
    expect(folders).toEqual([FOLDER]);
    expect(api.get).toHaveBeenCalledWith('/api/v1/folders/');
  });

  it('createFolder posts the name', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: FOLDER });
    const created = await createFolder('Invoices');
    expect(created).toEqual(FOLDER);
    expect(api.post).toHaveBeenCalledWith('/api/v1/folders/', {
      name: 'Invoices',
    });
  });

  it('renameFolder patches the name', async () => {
    const renamed = { ...FOLDER, name: 'Receipts' };
    vi.mocked(api.patch).mockResolvedValueOnce({ data: renamed });
    const result = await renameFolder('folder-1', 'Receipts');
    expect(result).toEqual(renamed);
    expect(api.patch).toHaveBeenCalledWith('/api/v1/folders/folder-1', {
      name: 'Receipts',
    });
  });

  it('deleteFolder calls delete', async () => {
    vi.mocked(api.delete).mockResolvedValueOnce({ data: null });
    await deleteFolder('folder-1');
    expect(api.delete).toHaveBeenCalledWith('/api/v1/folders/folder-1');
  });
});
