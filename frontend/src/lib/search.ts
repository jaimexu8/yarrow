import api from './api';

export type SearchHit = {
  id: string;
  filename: string;
};

export async function searchDocuments(q: string): Promise<SearchHit[]> {
  const res = await api.get<SearchHit[]>('/api/v1/search/', { params: { q } });
  return res.data;
}
