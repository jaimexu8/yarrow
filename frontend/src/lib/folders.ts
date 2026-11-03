import api from './api';

export type Folder = {
  id: string;
  name: string;
  created_at: string | null;
};

export async function listFolders(): Promise<Folder[]> {
  const res = await api.get<Folder[]>('/api/v1/folders/');
  return res.data;
}

export async function createFolder(name: string): Promise<Folder> {
  const res = await api.post<Folder>('/api/v1/folders/', { name });
  return res.data;
}

export async function renameFolder(id: string, name: string): Promise<Folder> {
  const res = await api.patch<Folder>(`/api/v1/folders/${id}`, { name });
  return res.data;
}

export async function deleteFolder(id: string): Promise<void> {
  await api.delete(`/api/v1/folders/${id}`);
}
