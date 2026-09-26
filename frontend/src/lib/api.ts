import axios from 'axios';

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
});

api.interceptors.request.use((config) => {
  const token =
    typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status !== 401 || typeof window === 'undefined') {
      return Promise.reject(error);
    }
    // A 401 only ends the session if it rejected the token we hold *now*.
    // - No token sent: it is the login form's "wrong password", and reloading
    //   would wipe out that message.
    // - A different token: a stale request from before a newer sign-in,
    //   which must not throw away the new session.
    const header = String(error.config?.headers?.Authorization ?? '');
    const sentToken = header.replace(/^Bearer /, '');
    if (!sentToken || sentToken !== localStorage.getItem('token')) {
      return Promise.reject(error);
    }
    localStorage.removeItem('token');
    const path = window.location.pathname;
    // Already on a sign-in page: clearing the token is enough.
    if (!path.startsWith('/login') && !path.startsWith('/register')) {
      window.location.href = `/login?next=${encodeURIComponent(path)}`;
    }
    return Promise.reject(error);
  }
);

export default api;
