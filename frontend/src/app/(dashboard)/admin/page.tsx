'use client';

import { useEffect, useState } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { RequireAdmin } from '@/components/auth/RequireAdmin';
import { Modal } from '@/components/ui/Modal';
import { Spinner } from '@/components/ui/Spinner';
import { useAuth } from '@/context/AuthContext';
import {
  getAdminStats,
  listAccounts,
  setAccountStatus,
  type AdminAccount,
  type AdminStats,
  type JobStatusCounts,
} from '@/lib/admin';
import { parseApiDate } from '@/lib/documents';
import { toApiError } from '@/lib/errors';
import { formatBytes } from '@/lib/uploads';
import { cn } from '@/lib/cn';
import { ActionMenu, ActionMenuItem } from '@/components/ui/ActionMenu';
import { Ban, Check, X, ArrowUpFromDot } from 'lucide-react';

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
  pending,
}: {
  account: AdminAccount;
  onStatusChange: (accountId: string, isActive: boolean) => void;
  onAdminStatusChange: (accountId: string, isAdmin: boolean) => void;
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

  actions.push({
    label: account.is_admin ? 'Demote from Admin' : 'Promote to Admin',
    icon: ArrowUpFromDot,
    destructive: false,
    keepFocus: true,
    onSelect: () => onAdminStatusChange(account.id, !account.is_admin),
  });

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
  pendingAccountId,
}: {
  accounts: AdminAccount[];
  onStatusChange: (accountId: string, isActive: boolean) => void;
  onAdminStatusChange: (accountId: string, isAdmin: boolean) => void;
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
              onAdminStatusChange={onAdminStatusChange}
              pending={pendingAccountId === account.id}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AccountsSection({
  accounts,
  onStatusChange,
  onAdminStatusChange,
  pendingAccountId,
}: {
  accounts: AdminAccount[];
  onStatusChange: (accountId: string, isActive: boolean) => void;
  onAdminStatusChange: (accountId: string, isAdmin: boolean) => void;
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
          onAdminStatusChange={onAdminStatusChange}
          pendingAccountId={pendingAccountId}
        />
      )}
    </section>
  );
}

function AdminBody() {
  const { user, logout } = useAuth();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [accounts, setAccounts] = useState<AdminAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingAccountId, setPendingAccountId] = useState<string | null>(null);
  const [confirmModalOpen, setConfirmModalOpen] = useState(false);
  const [selfDemotionError, setSelfDemotionError] = useState<string | null>(
    null
  );

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
      <div className="space-y-3">
        {actionError && <Alert>{actionError}</Alert>}
        <AccountsSection
          accounts={accounts}
          onStatusChange={changeAccountStatus}
          onAdminStatusChange={onAdminStatusChange}
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
            System totals and every user account.
          </p>
        </div>
        <AdminBody />
      </div>
    </RequireAdmin>
  );
}
