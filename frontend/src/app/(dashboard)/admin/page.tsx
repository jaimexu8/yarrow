'use client';

import { useEffect, useState } from 'react';
import { SystemStatusPanel } from '@/components/admin/SystemStatusPanel';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { RequireAdmin } from '@/components/auth/RequireAdmin';
import { Modal } from '@/components/ui/Modal';
import { Spinner } from '@/components/ui/Spinner';
import { useAuth } from '@/context/AuthContext';
import {
  deleteAdminAccount,
  getAdminStats,
  isActiveJob,
  jobQueuePlace,
  JOBS_POLL_INTERVAL_MS,
  jobStage,
  listAccounts,
  listAdminJobs,
  setAccountStatus,
  type AdminAccount,
  type AdminJob,
  type AdminStats,
  type JobStatusCounts,
} from '@/lib/admin';
import { parseApiDate, STATUS_LABELS } from '@/lib/documents';
import { toApiError } from '@/lib/errors';
import { formatBytes } from '@/lib/storage';
import { cn } from '@/lib/cn';
import { ActionMenu, ActionMenuItem } from '@/components/ui/ActionMenu';
import { Ban, Check, Trash, X, ArrowUpFromDot } from 'lucide-react';
import {
  JobActionButtons,
  JobActionModal,
  StuckBadge,
  type JobAction,
} from '@/components/admin/JobActionModals';

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
  onAdminStatusChange,
  onDelete,
  pending,
  isSelf,
}: {
  account: AdminAccount;
  onStatusChange: (accountId: string, isActive: boolean) => void;
  onAdminStatusChange: (accountId: string, isAdmin: boolean) => void;
  onDelete: (account: AdminAccount) => void;
  pending: boolean;
  isSelf: boolean;
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

  actions.push({
    label: account.is_admin ? 'Demote from Admin' : 'Promote to Admin',
    icon: ArrowUpFromDot,
    keepFocus: true,
    onSelect: () => onAdminStatusChange(account.id, !account.is_admin),
  });

  if (!isSelf) {
    actions.push({
      label: 'Delete',
      icon: Trash,
      destructive: true,
      onSelect: () => onDelete(account),
    });
  }

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
  onAdminStatusChange,
  onDelete,
  pendingAccountId,
  currentUserId,
}: {
  accounts: AdminAccount[];
  onStatusChange: (accountId: string, isActive: boolean) => void;
  onAdminStatusChange: (accountId: string, isAdmin: boolean) => void;
  onDelete: (account: AdminAccount) => void;
  pendingAccountId: string | null;
  currentUserId: string | undefined;
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
              onAdminStatusChange={onAdminStatusChange}
              onDelete={onDelete}
              pending={pendingAccountId === account.id}
              isSelf={currentUserId === account.id}
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

function JobTableRow({
  job,
  onAction,
}: {
  job: AdminJob;
  onAction: (action: JobAction) => void;
}) {
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
      <td className={cn(CELL, 'whitespace-nowrap text-slate-700')}>
        {jobStatusLabel(job.status)}
        {job.is_stuck && <StuckBadge />}
      </td>
      <td className={cn(CELL, 'tabular-nums text-slate-700')}>
        {jobQueuePlace(job)}
      </td>
      <td className={cn(CELL, 'text-slate-700')}>{jobStage(job)}</td>
      <td className={cn(CELL, 'text-slate-600')}>{job.error_message || '—'}</td>
      <td className={cn(CELL, 'text-right')}>
        <JobActionButtons job={job} onAction={onAction} />
      </td>
    </tr>
  );
}

function JobTable({
  jobs,
  onAction,
}: {
  jobs: AdminJob[];
  onAction: (action: JobAction) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[56rem] border-separate border-spacing-y-2 text-sm">
        <thead>
          <tr>
            <th className={HEAD}>Owner</th>
            <th className={HEAD}>Document</th>
            <th className={HEAD}>Status</th>
            <th className={HEAD}>Queue</th>
            <th className={HEAD}>Stage</th>
            <th className={HEAD}>Failure reason</th>
            <th className={cn(HEAD, 'text-right')}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <JobTableRow job={job} key={job.id} onAction={onAction} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JobsSection({
  jobs,
  onAction,
}: {
  jobs: AdminJob[];
  onAction: (action: JobAction) => void;
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-slate-900">Jobs</h2>
      {jobs.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-600">
          No processing jobs yet.
        </p>
      ) : (
        <JobTable jobs={jobs} onAction={onAction} />
      )}
    </section>
  );
}

function AccountsSection({
  accounts,
  onStatusChange,
  onAdminStatusChange,
  onDelete,
  pendingAccountId,
  currentUserId,
}: {
  accounts: AdminAccount[];
  onStatusChange: (accountId: string, isActive: boolean) => void;
  onAdminStatusChange: (accountId: string, isAdmin: boolean) => void;
  onDelete: (account: AdminAccount) => void;
  pendingAccountId: string | null;
  currentUserId: string | undefined;
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
          onAdminStatusChange={onAdminStatusChange}
          onDelete={onDelete}
          pendingAccountId={pendingAccountId}
          currentUserId={currentUserId}
        />
      )}
    </section>
  );
}

function AdminBody() {
  const { user, logout } = useAuth();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [accounts, setAccounts] = useState<AdminAccount[] | null>(null);
  const [jobs, setJobs] = useState<AdminJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingAccountId, setPendingAccountId] = useState<string | null>(null);
  const [accountToDelete, setAccountToDelete] = useState<AdminAccount | null>(
    null
  );
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [confirmModalOpen, setConfirmModalOpen] = useState(false);
  const [selfDemotionError, setSelfDemotionError] = useState<string | null>(
    null
  );
  const [jobAction, setJobAction] = useState<JobAction | null>(null);
  const [jobsPolls, setJobsPolls] = useState(0);

  async function reloadJobs() {
    const [nextStats, nextJobs] = await Promise.all([
      getAdminStats(),
      listAdminJobs(),
    ]);
    setStats(nextStats);
    setJobs(nextJobs);
  }

  async function changeAccountStatus(accountId: string, isActive: boolean) {
    setPendingAccountId(accountId);
    setActionError(null);
    try {
      await setAccountStatus(accountId, { is_active: isActive });
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

  async function confirmDelete() {
    if (!accountToDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteAdminAccount(accountToDelete.id);
      setAccounts(
        (current) =>
          current?.filter((account) => account.id !== accountToDelete.id) ??
          null
      );
      setAccountToDelete(null);
      setStats(await getAdminStats());
    } catch (err) {
      setDeleteError(toApiError(err).message);
    } finally {
      setDeleting(false);
    }
  }

  async function saveAdminStatus(accountId: string, isAdmin: boolean) {
    setPendingAccountId(accountId);
    setActionError(null);
    try {
      await setAccountStatus(accountId, { is_admin: isAdmin });
      if (accountId === user?.id && !isAdmin) {
        await logout();
        return;
      }
      setAccounts(
        (current) =>
          current?.map((account) =>
            account.id === accountId
              ? { ...account, is_admin: isAdmin }
              : account
          ) ?? null
      );
    } catch (err) {
      setActionError(toApiError(err).message);
    } finally {
      setPendingAccountId(null);
    }
  }

  function onAdminStatusChange(accountId: string, isAdmin: boolean) {
    if (accountId === user?.id && !isAdmin) {
      setSelfDemotionError(null);
      setConfirmModalOpen(true);
      return;
    }
    void saveAdminStatus(accountId, isAdmin);
  }

  async function confirmDemoteSelf() {
    if (!user) return;
    setPendingAccountId(user.id);
    setSelfDemotionError(null);
    try {
      await setAccountStatus(user.id, { is_admin: false });
      setConfirmModalOpen(false);
      await logout();
    } catch (err) {
      setSelfDemotionError(toApiError(err).message);
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

  useEffect(() => {
    if (!jobs?.some(isActiveJob)) return;
    let current = true;
    const timer = setTimeout(() => {
      Promise.all([getAdminStats(), listAdminJobs()])
        .then(([nextStats, nextJobs]) => {
          if (!current) return;
          setStats(nextStats);
          setJobs(nextJobs);
        })
        .catch(() => {})
        .finally(() => {
          if (current) setJobsPolls((count) => count + 1);
        });
    }, JOBS_POLL_INTERVAL_MS);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [jobs, jobsPolls]);

  if (error) return <Alert>{error}</Alert>;
  if (!stats || !accounts || !jobs) {
    return (
      <div className="flex justify-center py-12 text-slate-500">
        <Spinner label="Loading admin dashboard" />
      </div>
    );
  }

  const deleteLabel = accountToDelete
    ? accountToDelete.name || accountToDelete.email
    : '';

  return (
    <>
      <Modal
        open={confirmModalOpen}
        onClose={() => setConfirmModalOpen(false)}
        title="Demote your administrator account?"
        dismissible={pendingAccountId !== user?.id}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setConfirmModalOpen(false)}
              disabled={pendingAccountId === user?.id}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={confirmDemoteSelf}
              loading={pendingAccountId === user?.id}
            >
              Demote and log out
            </Button>
          </>
        }
      >
        <p>
          You will lose administrator access and be logged out immediately. You
          will not be able to access this page again unless another
          administrator restores your role.
        </p>
        {selfDemotionError && (
          <p role="alert" className="mt-3 text-red-700">
            {selfDemotionError}
          </p>
        )}
      </Modal>
      <StatsSection stats={stats} />
      <SystemStatusPanel />
      <JobsSection jobs={jobs} onAction={setJobAction} />
      <JobActionModal
        key={jobAction ? `${jobAction.kind}-${jobAction.job.id}` : 'none'}
        action={jobAction}
        onClose={() => setJobAction(null)}
        onDone={() =>
          reloadJobs().catch((err) => setActionError(toApiError(err).message))
        }
      />
      <div className="space-y-3">
        {actionError && <Alert>{actionError}</Alert>}
        <AccountsSection
          accounts={accounts}
          onStatusChange={changeAccountStatus}
          onAdminStatusChange={onAdminStatusChange}
          onDelete={(account) => {
            setDeleteError(null);
            setAccountToDelete(account);
          }}
          pendingAccountId={pendingAccountId}
          currentUserId={user?.id}
        />
      </div>
      <Modal
        open={accountToDelete !== null}
        onClose={() => {
          if (!deleting) setAccountToDelete(null);
        }}
        dismissible={!deleting}
        title="Delete this account?"
        footer={
          <>
            <Button
              variant="secondary"
              className="sm:w-auto"
              onClick={() => setAccountToDelete(null)}
              disabled={deleting}
              data-autofocus
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              className="sm:w-auto"
              onClick={confirmDelete}
              loading={deleting}
            >
              {deleteError ? 'Try again' : 'Delete'}
            </Button>
          </>
        }
      >
        <p className="break-words">
          This removes {deleteLabel} and all of their documents. This can&apos;t
          be undone.
        </p>
        {deleteError && (
          <Alert className="mt-4" tone="error">
            {deleteError}
          </Alert>
        )}
      </Modal>
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
            System totals, service health, every user account, and processing
            jobs.
          </p>
        </div>
        <AdminBody />
      </div>
    </RequireAdmin>
  );
}
