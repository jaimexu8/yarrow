'use client';

import { useAuth } from '@/context/AuthContext';

export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();

  if (!user?.is_admin) {
    return (
      <div className="mx-auto max-w-md space-y-2 py-16 text-center">
        <h1 className="text-lg font-semibold text-slate-900">Access denied</h1>
        <p className="text-sm text-slate-600">
          This page is only available to administrators.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
