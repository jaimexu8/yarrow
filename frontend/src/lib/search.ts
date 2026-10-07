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

export type SearchFilters = {
  /** UTC calendar date, YYYY-MM-DD. Documents uploaded on or after this day. */
  dateFrom?: string;
  /** UTC calendar date, YYYY-MM-DD. Includes the whole day. */
  dateTo?: string;
  /** MIME type stored on the document, e.g. application/pdf. */
  fileType?: string;
};

export async function searchDocuments(
  q: string,
  filters?: SearchFilters
): Promise<SearchHit[]> {
  const params: Record<string, string> = { q };
  if (filters?.dateFrom) params.date_from = filters.dateFrom;
  if (filters?.dateTo) params.date_to = filters.dateTo;
  if (filters?.fileType) params.file_type = filters.fileType;
  const res = await api.get<SearchHit[]>('/api/v1/search/', { params });
  return res.data;
}
