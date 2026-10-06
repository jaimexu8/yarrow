'use client';

import { useEffect, useState } from 'react';
import { Alert } from '@/components/ui/Alert';
import { RequireAdmin } from '@/components/auth/RequireAdmin';
import { Spinner } from '@/components/ui/Spinner';
import {
  getAdminStats,
  listAccounts,
  type AdminAccount,
  type AdminStats,
  type JobStatusCounts,
} from '@/lib/admin';
import { parseApiDate } from '@/lib/documents';
import { toApiError } from '@/lib/errors';
import { formatBytes } from '@/lib/uploads';
import { cn } from '@/lib/cn';

const CELL =
  'border-y border-slate-200 bg-white px-3 py-3 sm:px-4 align-middle first:rounded-l-xl first:border-l last:rounded-r-xl last:border-r';
const HEAD = 'px-3 pb-1 text-left sm:px-4 text-xs font-medium text-slate-500';
const CARD = 'rounded-xl border border-slate-200 bg-white px-4 py-3';

const JOB_STATUS_ORDER: {
  key: keyof JobStatusCounts;
  label: string;
}[] = [
  { key: 'queued', label: 'Queued' },
  { key: 'processing', label: 'Processing' },
  { key: 'completed', label: 'Completed' },
  { key: 'failed', label: 'Failed' },
  { key: 'canceled', label: 'Canceled' },
];

function joinedOn(value: string | null): string {
  if (!value) return 'Unknown';
  return parseApiDate(value).toLocaleDateString(undefined, {
    dateStyle: 'medium',
  });
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className={CARD}>
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">
        {value}
      </p>
    </div>
  );
}

function StatsSection({ stats }: { stats: AdminStats }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-slate-900">Overview</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard label="Users" value={stats.user_count} />
        <StatCard label="Documents" value={stats.document_count} />
        <StatCard
          label="Storage"
          value={formatBytes(stats.storage_used_bytes)}
        />
      </div>
      <div className={CARD}>
        <p className="text-xs font-medium text-slate-500">Jobs</p>
        <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
          {JOB_STATUS_ORDER.map((item) => (
            <div key={item.key}>
              <dt className="text-xs text-slate-500">{item.label}</dt>
              <dd className="mt-0.5 text-lg font-semibold tabular-nums text-slate-900">
                {stats.jobs_by_status[item.key]}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
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
              <td
                className={cn(CELL, 'text-right tabular-nums text-slate-700')}
              >
                {account.document_count}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AccountsSection({ accounts }: { accounts: AdminAccount[] }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-slate-900">Accounts</h2>
      {accounts.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-600">
          No accounts yet.
        </p>
      ) : (
        <AccountTable accounts={accounts} />
      )}
    </section>
  );
}

function AdminBody() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [accounts, setAccounts] = useState<AdminAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    Promise.all([getAdminStats(), listAccounts()])
      .then(([nextStats, nextAccounts]) => {
        if (!current) return;
        setStats(nextStats);
        setAccounts(nextAccounts);
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
  if (!stats || !accounts) {
    return (
      <div className="flex justify-center py-12 text-slate-500">
        <Spinner label="Loading admin dashboard" />
      </div>
    );
  }

  return (
    <>
      <StatsSection stats={stats} />
      <AccountsSection accounts={accounts} />
    </>
  );
}

export default function AdminPage() {
  return (
    <RequireAdmin>
      <div className="space-y-8">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Admin
          </h1>
          <p className="text-sm text-slate-600">
            System totals and every user account.
          </p>
        </div>
        <AdminBody />
      </div>
    </RequireAdmin>
  );
}
