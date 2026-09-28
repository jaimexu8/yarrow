import api from './api';

export type Document = {
  id: string;
  filename: string;
  file_size_bytes: number;
  file_type: string;
  page_count: number | null;
  status: string | null;
  error_message: string | null;
  created_at: string | null;
};

export async function listDocuments(): Promise<Document[]> {
  const res = await api.get<Document[]>('/api/v1/documents/');
  return res.data;
}
