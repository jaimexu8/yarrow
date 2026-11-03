'use client';

import { useEffect, useState } from 'react';
import { Alert } from '@/components/ui/Alert';
import { RequireAdmin } from '@/components/auth/RequireAdmin';
import { Spinner } from '@/components/ui/Spinner';
import {
  getAdminStats,
  jobQueuePlace,
  jobStage,
  listAccounts,
  listAdminJobs,
  setAccountActive,
  type AdminAccount,
  type AdminJob,
  type AdminStats,
  type JobStatusCounts,
} from '@/lib/admin';
import { parseApiDate, STATUS_LABELS } from '@/lib/documents';
import { toApiError } from '@/lib/errors';
import { formatBytes } from '@/lib/uploads';
import { cn } from '@/lib/cn';
import { ActionMenu, ActionMenuItem } from '@/components/ui/ActionMenu';
import { Ban, Check, X } from 'lucide-react';

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

function AccountTableRow({
  account,
  onStatusChange,
  pending,
}: {
  account: AdminAccount;
  onStatusChange: (accountId: string, isActive: boolean) => void;
  pending: boolean;
}) {
  const actions: ActionMenuItem[] = [
    {
      label: account.is_active ? 'Disable' : 'Enable',
      icon: account.is_active ? Ban : Check,
      destructive: account.is_active,
      keepFocus: true,
      onSelect: () => onStatusChange(account.id, !account.is_active),
    },
  ];

  return (
    <tr>
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
      <td className={cn(CELL, 'text-right')}>
        <span className="flex justify-end">
          {account.is_active ? (
            <Check aria-hidden="true" />
          ) : (
            <X aria-hidden="true" />
          )}
        </span>
      </td>
      <td className={cn(CELL, 'text-right')}>
        {pending ? (
          <Spinner label="Updating account" />
        ) : (
          <ActionMenu
            label={`More actions for ${account.name || account.email}`}
            items={actions}
          />
        )}
      </td>
    </tr>
  );
}

function AccountTable({
  accounts,
  onStatusChange,
  pendingAccountId,
}: {
  accounts: AdminAccount[];
  onStatusChange: (accountId: string, isActive: boolean) => void;
  pendingAccountId: string | null;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-separate border-spacing-y-2 text-sm">
        <thead>
          <tr>
            <th className={HEAD}>Name</th>
            <th className={HEAD}>Email</th>
            <th className={HEAD}>Joined</th>
            <th className={cn(HEAD, 'text-right')}>Documents</th>
            <th className={cn(HEAD, 'text-right')}>Enabled</th>
            <th className={cn(HEAD, 'text-right')}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => (
            <AccountTableRow
              account={account}
              key={account.id}
              onStatusChange={onStatusChange}
              pending={pendingAccountId === account.id}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function jobStatusLabel(status: AdminJob['status']): string {
  if (!status) return '—';
  return STATUS_LABELS[status] ?? status;
}

function JobTableRow({ job }: { job: AdminJob }) {
  return (
    <tr>
      <td className={CELL}>
        <span className="font-medium text-slate-900">
          {job.owner_name || job.owner_email}
        </span>
        {job.owner_name && (
          <p className="text-xs text-slate-500">{job.owner_email}</p>
        )}
      </td>
      <td className={cn(CELL, 'text-slate-700')}>{job.filename}</td>
      <td className={cn(CELL, 'text-slate-700')}>
        {jobStatusLabel(job.status)}
      </td>
      <td className={cn(CELL, 'tabular-nums text-slate-700')}>
        {jobQueuePlace(job)}
      </td>
      <td className={cn(CELL, 'text-slate-700')}>{jobStage(job)}</td>
      <td className={cn(CELL, 'text-slate-600')}>{job.error_message || '—'}</td>
    </tr>
  );
}

function JobTable({ jobs }: { jobs: AdminJob[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[48rem] border-separate border-spacing-y-2 text-sm">
        <thead>
          <tr>
            <th className={HEAD}>Owner</th>
            <th className={HEAD}>Document</th>
            <th className={HEAD}>Status</th>
            <th className={HEAD}>Queue</th>
            <th className={HEAD}>Stage</th>
            <th className={HEAD}>Failure reason</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <JobTableRow job={job} key={job.id} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JobsSection({ jobs }: { jobs: AdminJob[] }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-slate-900">Jobs</h2>
      {jobs.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-600">
          No processing jobs yet.
        </p>
      ) : (
        <JobTable jobs={jobs} />
      )}
    </section>
  );
}

function AccountsSection({
  accounts,
  onStatusChange,
  pendingAccountId,
}: {
  accounts: AdminAccount[];
  onStatusChange: (accountId: string, isActive: boolean) => void;
  pendingAccountId: string | null;
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-slate-900">Accounts</h2>
      {accounts.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-600">
          No accounts yet.
        </p>
      ) : (
        <AccountTable
          accounts={accounts}
          onStatusChange={onStatusChange}
          pendingAccountId={pendingAccountId}
        />
      )}
    </section>
  );
}

function AdminBody() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [accounts, setAccounts] = useState<AdminAccount[] | null>(null);
  const [jobs, setJobs] = useState<AdminJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingAccountId, setPendingAccountId] = useState<string | null>(null);

  async function changeAccountStatus(accountId: string, isActive: boolean) {
    setPendingAccountId(accountId);
    setActionError(null);
    try {
      await setAccountActive(accountId, isActive);
      setAccounts(
        (current) =>
          current?.map((account) =>
            account.id === accountId
              ? { ...account, is_active: isActive }
              : account
          ) ?? null
      );
    } catch (err) {
      setActionError(toApiError(err).message);
    } finally {
      setPendingAccountId(null);
    }
  }

  useEffect(() => {
    let current = true;
    Promise.all([getAdminStats(), listAccounts(), listAdminJobs()])
      .then(([nextStats, nextAccounts, nextJobs]) => {
        if (!current) return;
        setStats(nextStats);
        setAccounts(nextAccounts);
        setJobs(nextJobs);
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
  if (!stats || !accounts || !jobs) {
    return (
      <div className="flex justify-center py-12 text-slate-500">
        <Spinner label="Loading admin dashboard" />
      </div>
    );
  }

  return (
    <>
      <StatsSection stats={stats} />
      <JobsSection jobs={jobs} />
      <div className="space-y-3">
        {actionError && <Alert>{actionError}</Alert>}
        <AccountsSection
          accounts={accounts}
          onStatusChange={changeAccountStatus}
          pendingAccountId={pendingAccountId}
        />
      </div>
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
            System totals, every user account, and processing jobs.
          </p>
        </div>
        <AdminBody />
      </div>
    </RequireAdmin>
  );
}
