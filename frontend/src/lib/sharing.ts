import api from './api';

export type SharePermission = 'view' | 'review';

export type ShareInfo = {
  id: string;
  document_id: string;
  shared_with_user_id: string;
  shared_with_email: string;
  shared_with_name: string | null;
  permission: SharePermission;
  created_at: string | null;
};

export async function listDocumentShares(
  documentId: string
): Promise<ShareInfo[]> {
  const res = await api.get<ShareInfo[]>(
    `/api/v1/documents/${documentId}/shares`
  );
  return res.data;
}

export async function shareDocument(
  documentId: string,
  email: string,
  permission: SharePermission = 'view'
): Promise<ShareInfo> {
  const res = await api.post<ShareInfo>(
    `/api/v1/documents/${documentId}/share`,
    {
      email,
      permission,
    }
  );
  return res.data;
}

export async function revokeDocumentShare(
  documentId: string,
  shareId: string
): Promise<void> {
  await api.delete(`/api/v1/documents/${documentId}/shares/${shareId}`);
}

export async function updateSharePermission(
  documentId: string,
  shareId: string,
  permission: SharePermission
): Promise<ShareInfo> {
  const res = await api.patch<ShareInfo>(
    `/api/v1/documents/${documentId}/shares/${shareId}`,
    {
      permission,
    }
  );
  return res.data;
}
