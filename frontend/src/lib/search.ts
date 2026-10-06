import api from './api';

export type SearchSnippet = {
  region_id: string;
  page_number: number;
  text: string;
  match_start: number;
  match_end: number;
};

export type SearchHit = {
  id: string;
  filename: string;
  snippets: SearchSnippet[];
};

export async function searchDocuments(q: string): Promise<SearchHit[]> {
  const res = await api.get<SearchHit[]>('/api/v1/search/', { params: { q } });
  return res.data;
}
