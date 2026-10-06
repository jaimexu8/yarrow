'use client';

import { useEffect, useState } from 'react';
import { Alert } from '@/components/ui/Alert';
import { RequireAdmin } from '@/components/auth/RequireAdmin';
import { Spinner } from '@/components/ui/Spinner';
import { listAccounts, type AdminAccount } from '@/lib/admin';
import { parseApiDate } from '@/lib/documents';
import { toApiError } from '@/lib/errors';
import { cn } from '@/lib/cn';

const CELL =
  'border-y border-slate-200 bg-white px-3 py-3 sm:px-4 align-middle first:rounded-l-xl first:border-l last:rounded-r-xl last:border-r';
const HEAD = 'px-3 pb-1 text-left sm:px-4 text-xs font-medium text-slate-500';

function joinedOn(value: string | null): string {
  if (!value) return 'Unknown';
  return parseApiDate(value).toLocaleDateString(undefined, {
    dateStyle: 'medium',
  });
}

function AccountTable({ accounts }: { accounts: AdminAccount[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-separate border-spacing-y-2 text-sm">
        <thead>
          <tr>
            <th className={HEAD}>Name</th>
            <th className={HEAD}>Email</th>
            <th className={HEAD}>Joined</th>
            <th className={cn(HEAD, 'text-right')}>Documents</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => (
            <tr key={account.id}>
              <td className={CELL}>
                <span className="font-medium text-slate-900">
                  {account.name || 'No name'}
                </span>
                {account.is_admin && (
                  <span className="ml-2 inline-flex items-center rounded-full border border-slate-300 bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                    Admin
                  </span>
                )}
              </td>
              <td className={cn(CELL, 'text-slate-700')}>{account.email}</td>
              <td className={cn(CELL, 'text-slate-600')}>
                {joinedOn(account.created_at)}
              </td>
              <td className={cn(CELL, 'text-right tabular-nums text-slate-700')}>
                {account.document_count}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AccountsBody() {
  const [accounts, setAccounts] = useState<AdminAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    listAccounts()
      .then((rows) => {
        if (current) setAccounts(rows);
      })
      .catch((err) => {
        if (!current) return;
        const apiError = toApiError(err);
        setError(
          apiError.status === 403
            ? 'This page is only available to administrators.'
            : apiError.message
        );
      });
    return () => {
      current = false;
    };
  }, []);

  if (error) return <Alert>{error}</Alert>;
  if (!accounts) {
    return (
      <div className="flex justify-center py-12 text-slate-500">
        <Spinner label="Loading accounts" />
      </div>
    );
  }

  if (accounts.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-600">
        No accounts yet.
      </p>
    );
  }

  return <AccountTable accounts={accounts} />;
}

export default function AdminPage() {
  return (
    <RequireAdmin>
      <div className="space-y-8">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Accounts
          </h1>
          <p className="text-sm text-slate-600">
            All user accounts on this system.
          </p>
        </div>
        <AccountsBody />
      </div>
    </RequireAdmin>
  );
}
