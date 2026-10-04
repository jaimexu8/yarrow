import api from './api';
import type { TableNode } from './viewer';

export type TableMutationResult = {
  changed: boolean;
  table_count_before: number;
  table_count_after: number;
  tables: TableNode[];
  document_updated_at: string | null;
};

// A pair of tables that may be merged
export type MergeCandidate = {
  previous_table_id: string;
  next_table_id: string;
  boundary_page: number;
};

export type MergeCandidates = {
  candidates: MergeCandidate[];
  splittable_table_ids: string[];
  can_edit: boolean;
};

/**
 * Which table pairs can be merged and which tables can be split, computed by
 * the server with the same rule the merge endpoint enforces, so a merge it
 * lists will not be refused as not consecutive.
 */
export async function getMergeCandidates(
  documentId: string
): Promise<MergeCandidates> {
  const res = await api.get<MergeCandidates>(
    `/api/v1/documents/${documentId}/tables/merge-candidates`
  );
  return res.data;
}

/** Merge one consecutive pair of tables into a single table. */
export async function mergeTablePair(
  documentId: string,
  previousTableId: string,
  nextTableId: string
): Promise<TableMutationResult> {
  const res = await api.post<TableMutationResult>(
    `/api/v1/documents/${documentId}/tables/merge`,
    { previous_table_id: previousTableId, next_table_id: nextTableId }
  );
  return res.data;
}

export async function mergeConsecutiveTables(
  documentId: string
): Promise<TableMutationResult> {
  const res = await api.post<TableMutationResult>(
    `/api/v1/documents/${documentId}/tables/merge-consecutive`
  );
  return res.data;
}

export async function splitConsecutiveTables(
  documentId: string
): Promise<TableMutationResult> {
  const res = await api.post<TableMutationResult>(
    `/api/v1/documents/${documentId}/tables/split-consecutive`
  );
  return res.data;
}

/** Split one table that spans pages into one table per page. */
export async function splitTable(
  documentId: string,
  tableId: string
): Promise<TableMutationResult> {
  const res = await api.post<TableMutationResult>(
    `/api/v1/documents/${documentId}/tables/${tableId}/split`
  );
  return res.data;
}
