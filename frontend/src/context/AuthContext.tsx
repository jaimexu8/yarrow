'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { fetchMe, type User } from '../lib/auth';

type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated';

type AuthContextType = {
  user: User | null;
  /**
   * 'loading' until the stored token (if any) has been checked. Protected
   * pages wait for this before deciding to redirect, so a signed-in user is
   * never bounced to /login on refresh.
   */
  status: AuthStatus;
  isAuthenticated: boolean;
  /** Store the token and load the user it belongs to. */
  login: (token: string) => Promise<User>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');
  // Bumped by every session change. A /me request only applies its result if
  // no newer change started meanwhile, so a slow check from page load can
  // never overwrite (or sign out) a sign-in that finished after it began.
  const generation = useRef(0);

  useEffect(() => {
    const gen = ++generation.current;
    const token = localStorage.getItem('token');
    if (!token) {
      setStatus('unauthenticated');
      return;
    }
    fetchMe()
      .then((me) => {
        if (gen !== generation.current) return;
        setUser(me);
        setStatus('authenticated');
      })
      .catch(() => {
        if (gen !== generation.current) return;
        localStorage.removeItem('token');
        setStatus('unauthenticated');
      });
  }, []);

  const login = useCallback(async (token: string) => {
    const gen = ++generation.current;
    localStorage.setItem('token', token);
    try {
      const me = await fetchMe();
      if (gen === generation.current) {
        setUser(me);
        setStatus('authenticated');
      }
      return me;
    } catch (error) {
      if (gen === generation.current) {
        localStorage.removeItem('token');
        setUser(null);
        setStatus('unauthenticated');
      }
      throw error;
    }
  }, []);

  const logout = useCallback(() => {
    generation.current += 1;
    localStorage.removeItem('token');
    setUser(null);
    setStatus('unauthenticated');
    window.location.href = '/login';
  }, []);

  const value = useMemo(
    () => ({
      user,
      status,
      isAuthenticated: status === 'authenticated',
      login,
      logout,
    }),
    [user, status, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
