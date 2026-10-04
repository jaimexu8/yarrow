import api from './api';
import type { TableNode } from './viewer';

export type TableMutationResult = {
  changed: boolean;
  table_count_before: number;
  table_count_after: number;
  tables: TableNode[];
  document_updated_at: string | null;
};

export async function mergeTables(
  documentId: string
): Promise<TableMutationResult> {
  const res = await api.post<TableMutationResult>(
    `/api/v1/documents/${documentId}/tables/merge-consecutive`
  );
  return res.data;
}

export async function splitTables(
  documentId: string
): Promise<TableMutationResult> {
  const res = await api.post<TableMutationResult>(
    `/api/v1/documents/${documentId}/tables/split-consecutive`
  );
  return res.data;
}
