import api from './api';

export type TableCellMatch = {
  table_id: string;
  region_table_id: string;
  cell_id: string;
  row: number;
  col: number;
  row_span: number;
  col_span: number;
  is_header: boolean;
  column_header: string | null;
};

export type SearchSnippet = {
  region_id: string;
  page_number: number;
  text: string;
  match_start: number;
  match_end: number;
  edited?: boolean;
  source: 'text' | 'table_cell';
  table_cell: TableCellMatch | null;
};

export type SearchHit = {
  id: string;
  filename: string;
  snippets: SearchSnippet[];
};

export type SearchSort = 'date' | 'name';

export type SearchScope = 'all' | 'text' | 'tables';

export type SearchFilters = {
  /** UTC calendar date, YYYY-MM-DD. Documents uploaded on or after this day. */
  dateFrom?: string;
  /** UTC calendar date, YYYY-MM-DD. Includes the whole day. */
  dateTo?: string;
  /** MIME type stored on the document, e.g. application/pdf. */
  fileType?: string;
  /** date = newest first. name = a-z. omitted means date. */
  sort?: SearchSort;
  scope?: SearchScope;
};

export async function searchDocuments(
  q: string,
  filters?: SearchFilters
): Promise<SearchHit[]> {
  const params: Record<string, string> = { q };
  if (filters?.dateFrom) params.date_from = filters.dateFrom;
  if (filters?.dateTo) params.date_to = filters.dateTo;
  if (filters?.fileType) params.file_type = filters.fileType;
  if (filters?.sort) params.sort = filters.sort;
  if (filters?.scope) params.scope = filters.scope;
  const res = await api.get<SearchHit[]>('/api/v1/search/', { params });
  return res.data;
}

/**
 * the single term to send to the viewer for highlighting.
 * It must match the first term of the parsed query (the snippet span is built
 * from it): a leading quoted phrase, whitespace-normalised, or otherwise the
 * first word. Empty when there is nothing usable.
 */
export function viewerHighlightTerm(q: string): string {
  const raw = (q ?? '').trim();
  if (!raw) return '';
  if (raw.startsWith('"')) {
    const end = raw.indexOf('"', 1);
    const phrase = (end === -1 ? raw.slice(1) : raw.slice(1, end))
      .split(/\s+/)
      .join(' ')
      .trim();
    return phrase;
  }
  return raw.split(/\s+/)[0] ?? '';
}

/**
 * true when a snippet's matched span differs from the query term it was
 * searched with, i.e. an OCR near match (e.g. query "2024" but the text is
 * "2O24", or a one-edit typo). Case- and internal-whitespace-insensitive, so
 * "Quarterly" vs "quarterly" and "quarterly  summary" vs "quarterly summary"
 * are not flagged.
 */
export function isApproxMatch(
  snippet: { text: string; match_start: number; match_end: number },
  term: string
): boolean {
  const norm = (s: string) => s.replace(/\s+/g, ' ').trim().toLowerCase();
  const termNorm = norm(term);
  if (!termNorm) return false;
  const matchedNorm = norm(
    snippet.text.slice(snippet.match_start, snippet.match_end)
  );
  return matchedNorm !== termNorm;
}
