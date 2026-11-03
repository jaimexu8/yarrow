import { afterEach, expect, it, vi } from 'vitest';
import api from './api';
import { deleteAdminAccount } from './admin';

vi.mock('./api', () => ({ default: { delete: vi.fn() } }));

afterEach(() => {
  vi.clearAllMocks();
});

it('deletes an account by id', async () => {
  vi.mocked(api.delete).mockResolvedValue({});
  await deleteAdminAccount('user-1');
  expect(api.delete).toHaveBeenCalledWith('/api/v1/admin/accounts/user-1');
});
