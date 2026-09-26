import api from './api';

/** Calls to the /api/v1/auth endpoints (US-1, US-2). */

export type User = {
  id: string;
  email: string;
  name?: string | null;
  is_admin: boolean;
  storage_used_bytes: number;
};

export type RegisterInput = {
  email: string;
  password: string;
  name?: string;
};

export async function registerAccount(input: RegisterInput): Promise<User> {
  const body = { ...input, name: input.name?.trim() || undefined };
  const res = await api.post<User>('/api/v1/auth/register', body);
  return res.data;
}

export async function verifyEmail(email: string, code: string): Promise<void> {
  await api.post('/api/v1/auth/verify', { email, code });
}

export async function resendVerification(email: string): Promise<void> {
  await api.post('/api/v1/auth/resend-verification', { email });
}

/** Returns the access token. The backend uses the OAuth2 password form. */
export async function loginRequest(
  email: string,
  password: string
): Promise<string> {
  const form = new URLSearchParams({ username: email, password });
  const res = await api.post<{ access_token: string }>(
    '/api/v1/auth/login',
    form
  );
  return res.data.access_token;
}

export async function fetchMe(): Promise<User> {
  const res = await api.get<User>('/api/v1/auth/me');
  return res.data;
}
