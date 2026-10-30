import api from './api';
import type { DocumentSummary } from './documents';

/** Page-pixel coordinates, origin top-left, in the page's width/height space. */
export type BBox = { x0: number; y0: number; x1: number; y1: number };

export type WarningNode = {
  warning_type: string | null;
  message: string | null;
};

export type RegionNode = {
  id: string;
  page_number: number;
  reading_order: number;

  // The OCR's block label as-is, e.g. "text", "paragraph_title", "table"
  region_type: string | null;
  bbox: BBox;

  // The layout model's confidence in this block's type and box
  confidence: number | null;

  // The active text; starts as the raw OCR output. Null for tables
  // and figures.
  text: string | null;

  // True once this region's text was manually corrected and the untouched
  // OCR output was preserved in original_text
  is_edited?: boolean;
  original_text?: string | null;

  text_confidence: number | null;
  table_id: string | null;
  region_table_id: string | null;
  is_first_table_part: boolean;
  image_key: string | null;
  caption: string | null;
};

// Regions with a confidence below this are flagged for review
export const LOW_CONFIDENCE_THRESHOLD = 0.5;

export function isLowConfidence(region: RegionNode): boolean {
  return (
    region.confidence != null && region.confidence < LOW_CONFIDENCE_THRESHOLD
  );
}

export type PageNode = {
  page_number: number;
  width: number | null;
  height: number | null;
  status: string | null;
  error_message: string | null;
  // Set only when the page was rotated before OCR. The viewer shows the
  // corrected upright image for those pages; null for everything else.
  image_key: string | null;
  regions: RegionNode[];
  warnings: WarningNode[];
};

export type TablePart = {
  region_table_id: string;
  region_id: string;
  page_number: number;
  reading_order: number;
  row_start: number;

  // Inclusive
  row_end: number;
  col_start: number;

  // Inclusive
  col_end: number;
};

export type TableCellNode = {
  // Global row within the logical table
  row: number;
  col: number;
  row_span: number;
  col_span: number;
  text: string | null;
  is_header: boolean;
  bbox: BBox;
  page_number: number;
  region_table_id: string;

  // Mean OCR score of the cell's words; null when it has none
  confidence: number | null;
};

export type TableNode = {
  id: string;
  title: string | null;
  row_count: number;
  col_count: number;
  is_stitched: boolean;
  has_spans: boolean;
  first_page: number;
  last_page: number;
  parts: TablePart[];
  row_sources: { row: number; page_number: number }[];
  cells: TableCellNode[];
};

export type DocumentTree = {
  schema_version: 1;
  generated_at: string;
  document: DocumentSummary;
  pages_included: number[];
  pages: PageNode[];
  // Whether the viewer may correct text or edit tables (owner/reviewer)
  can_edit?: boolean;
  // Always document-scoped
  tables: TableNode[];
  stats: {
    page_count: number;
    high_confidence_page_count: number;
    region_count: number;
    table_count: number;
    stitched_table_count: number;
    warning_count: number;
  };
};

/** The document's metadata, pages, regions and tables. */
export async function getDocumentTree(id: string): Promise<DocumentTree> {
  const res = await api.get<DocumentTree>(`/api/v1/documents/${id}/parsed`);
  return res.data;
}

export type OriginalFile = { data: ArrayBuffer; contentType: string };

/**
 * The originally uploaded file. Fetched through the API client rather than
 * pointed at by an <img> or <iframe>, because the endpoint needs the bearer
 * token, which only the client sends.
 */
export async function getOriginalFile(id: string): Promise<OriginalFile> {
  const res = await api.get<ArrayBuffer>(`/api/v1/documents/${id}/content`, {
    responseType: 'arraybuffer',
  });
  return {
    data: res.data,
    contentType: String(res.headers['content-type'] ?? ''),
  };
}

/**
 * The cropped picture of a figure region, for regions with an image_key.
 * Fetched through the API client for the same reason as the original file.
 */
export async function getRegionImage(
  documentId: string,
  regionId: string
): Promise<Blob> {
  const res = await api.get<Blob>(
    `/api/v1/documents/${documentId}/regions/${regionId}/image`,
    { responseType: 'blob' }
  );
  return res.data;
}

/**
 * The corrected upright image of a page that was rotated before OCR, for
 * pages with an image_key. Fetched through the API client for the same
 * reason as the original file.
 */
export async function getPageImage(
  documentId: string,
  pageNumber: number
): Promise<Blob> {
  const res = await api.get<Blob>(
    `/api/v1/documents/${documentId}/pages/${pageNumber}/image`,
    { responseType: 'blob' }
  );
  return res.data;
}
