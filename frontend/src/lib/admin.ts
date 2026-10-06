import api from './api';

export type AdminAccount = {
  id: string;
  name: string | null;
  email: string;
  created_at: string | null;
  is_admin: boolean;
  document_count: number;
};

export async function listAccounts(): Promise<AdminAccount[]> {
  const res = await api.get<AdminAccount[]>('/api/v1/admin/accounts');
  return res.data;
}
