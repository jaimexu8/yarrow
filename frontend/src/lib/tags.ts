import api from './api';
import type { DocumentSummary, DocumentTag } from './documents';

export const MAX_TAG_NAME_LENGTH = 40;

export type TagSummary = {
  id: string;
  name: string;
  document_count: number;
};

export function normalizeTagName(name: string): string {
  return name.trim();
}

/** client-side check that matches the server's tag name rules. */
export function tagNameError(name: string): string | null {
  const value = normalizeTagName(name);
  if (!value) return 'Tag name cannot be empty.';
  if (value.length > MAX_TAG_NAME_LENGTH) {
    return `Tag name must be ${MAX_TAG_NAME_LENGTH} characters or fewer.`;
  }
  return null;
}

export function uniqueTags(documents: DocumentSummary[]): DocumentTag[] {
  const seen = new Map<string, DocumentTag>();
  for (const doc of documents) {
    for (const tag of doc.tags ?? []) {
      if (!seen.has(tag.id)) seen.set(tag.id, tag);
    }
  }
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export function documentHasTag(doc: DocumentSummary, tagId: string): boolean {
  return (doc.tags ?? []).some((tag) => tag.id === tagId);
}

export async function listTags(): Promise<TagSummary[]> {
  const res = await api.get<TagSummary[]>('/api/v1/tags/');
  return res.data;
}

export async function addDocumentTag(
  documentId: string,
  name: string
): Promise<DocumentSummary> {
  const res = await api.post<DocumentSummary>(
    `/api/v1/documents/${documentId}/tags`,
    { name }
  );
  return res.data;
}

export async function removeDocumentTag(
  documentId: string,
  tagId: string
): Promise<void> {
  await api.delete(`/api/v1/documents/${documentId}/tags/${tagId}`);
}
