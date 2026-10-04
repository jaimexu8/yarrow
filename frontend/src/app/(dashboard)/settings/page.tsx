'use client';

import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { useAuth } from '@/context/AuthContext';
import { deleteAccount, updateAccount } from '@/lib/auth';
import { toApiError } from '@/lib/errors';
import { Input } from '@/components/ui/Input';
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export default function SettingsPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const { user, clearSession, refreshUser } = useAuth();

  // Start from the account's current details once they have loaded.
  const [filled, setFilled] = useState(false);
  useEffect(() => {
    if (user && !filled) {
      setName(user.name ?? '');
      setEmail(user.email);
      setFilled(true);
    }
  }, [user, filled]);

  async function handleSaveAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setSaveError(null);
    setFieldErrors({});
    setSaved(false);
    try {
      await updateAccount({
        name: name.trim() || null,
        email,
        current_password: currentPassword,
        ...(newPassword && { password: newPassword }),
      });
      setCurrentPassword('');
      setNewPassword('');
      setSaved(true);

      await refreshUser();
    } catch (err) {
      const apiError = toApiError(err);
      if (apiError.status === 403) {
        setFieldErrors({ current_password: 'Incorrect current password' });
      } else if (apiError.code === 'EMAIL_TAKEN') {
        setFieldErrors({ email: apiError.message });
      } else if (Object.keys(apiError.fields).length > 0) {
        setFieldErrors(apiError.fields);
      } else {
        setSaveError(apiError.message);
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteAccount() {
    setIsDeleting(true);

    try {
      await deleteAccount(password);
      clearSession();
      window.location.replace('/login');
      return;
    } catch (error) {
      const apiError = toApiError(error);
      console.error('Failed to delete account:', apiError.message);
      setError(`Failed to delete account: ${apiError.message}`);
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
        Settings
      </h1>

      <Modal
        open={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title="Confirm Account Deletion"
        footer={
          <>
            <Button variant="secondary" onClick={() => setIsModalOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={handleDeleteAccount}
              loading={isDeleting}
            >
              Delete Account
            </Button>
          </>
        }
      >
        <p className="text-sm text-slate-600">
          Enter your password to confirm account deletion.
        </p>

        <div className="mt-4">
          <PasswordInput
            id="current-password"
            label="Password"
            autoComplete="current-password"
            enterKeyHint="done"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            invalidMessage="Enter your password."
          />
        </div>

        {error && (
          <p role="alert" className="mt-3 text-sm text-red-600">
            {error}
          </p>
        )}
      </Modal>

      <SettingsCard id="account-information" title="Account information">
        <form onSubmit={handleSaveAccount}>
          <div className="divide-y divide-slate-200">
            <SettingRow
              title="Name"
              description="Your full name as it appears on your account."
            >
              <Input
                id="account-name"
                label="Name"
                hideLabel
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </SettingRow>
            <SettingRow
              title="Email"
              description="The address you sign in with and receive emails at."
            >
              <Input
                id="account-email"
                label="Email"
                hideLabel
                type="email"
                autoComplete="email"
                required
                value={email}
                error={fieldErrors.email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </SettingRow>
            <SettingRow
              title="Current password"
              description="Required to save any changes."
            >
              <PasswordInput
                id="account-current-password"
                label="Current password"
                hideLabel
                autoComplete="current-password"
                required
                value={currentPassword}
                error={fieldErrors.current_password}
                onChange={(e) => setCurrentPassword(e.target.value)}
              />
            </SettingRow>
            <SettingRow
              title="New password"
              description="Leave blank to keep your current password."
            >
              <PasswordInput
                id="account-new-password"
                label="New password"
                hideLabel
                autoComplete="new-password"
                value={newPassword}
                error={fieldErrors.password}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </SettingRow>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 rounded-b-lg border-t border-slate-200 bg-slate-50 px-6 py-4">
            {saveError && (
              <p role="alert" className="text-sm text-red-700">
                {saveError}
              </p>
            )}
            {saved && (
              <p role="status" className="text-sm text-emerald-700">
                Your account was updated.
              </p>
            )}
            <Button type="submit" className="w-auto" loading={saving}>
              Save changes
            </Button>
          </div>
        </form>
      </SettingsCard>

      <SettingsCard id="danger-zone" title="Danger zone" tone="danger">
        <SettingRow
          title="Delete account"
          description="Permanently delete your account and its associated data. This action cannot be undone."
        >
          <div className="flex sm:justify-end">
            <Button
              variant="danger"
              className="w-auto"
              onClick={() => {
                setError(null);
                setPassword('');
                setIsModalOpen(true);
              }}
            >
              Delete account
            </Button>
          </div>
        </SettingRow>
      </SettingsCard>
    </div>
  );
}

function SettingsCard({
  id,
  title,
  tone = 'default',
  children,
}: {
  id: string;
  title: string;
  tone?: 'default' | 'danger';
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="text-lg font-semibold text-slate-900">
        {title}
      </h2>
      <div
        className={cn(
          'mt-4 rounded-lg border bg-white',
          tone === 'danger' ? 'border-red-200' : 'border-slate-200'
        )}
      >
        {children}
      </div>
    </section>
  );
}

function SettingRow({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
      <div className="sm:max-w-sm">
        <h3 className="font-medium text-slate-900">{title}</h3>
        <p className="mt-1 text-sm text-slate-600">{description}</p>
      </div>
      <div className="w-full shrink-0 sm:w-80">{children}</div>
    </div>
  );
}
