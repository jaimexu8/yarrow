'use client';

import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Trash2, UserPlus, Users } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import {
  listDocumentShares,
  revokeDocumentShare,
  shareDocument,
  type ShareInfo,
  type SharePermission,
} from '@/lib/sharing';
import { toApiError } from '@/lib/errors';
import { cn } from '@/lib/cn';

interface ShareModalProps {
  documentId: string;
  documentTitle?: string;
  open: boolean;
  onClose: () => void;
}

export function ShareModal({
  documentId,
  documentTitle,
  open,
  onClose,
}: ShareModalProps) {
  const [shares, setShares] = useState<ShareInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState('');
  const [permission, setPermission] = useState<SharePermission>('view');
  const [sharing, setSharing] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const fetchShares = useCallback(async () => {
    if (!open) return;
    setLoading(true);
    try {
      const data = await listDocumentShares(documentId);
      setShares(data);
      setError(null);
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setLoading(false);
    }
  }, [documentId, open]);

  useEffect(() => {
    if (open) {
      setEmail('');
      setPermission('view');
      setFieldErrors({});
      setError(null);
      setSuccess(null);
      fetchShares();
    }
  }, [open, fetchShares]);

  async function handleShare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim()) return;

    setSharing(true);
    setError(null);
    setFieldErrors({});
    setSuccess(null);

    try {
      const newShare = await shareDocument(
        documentId,
        email.trim(),
        permission
      );
      setSuccess(`Shared with ${newShare.shared_with_email} successfully.`);
      setEmail('');
      await fetchShares();
    } catch (err) {
      const apiError = toApiError(err);
      if (Object.keys(apiError.fields).length > 0) {
        setFieldErrors(apiError.fields);
        // If there's a field-specific error message, surface it directly or show the general error
        setError(apiError.fields.email ?? apiError.message);
      } else {
        setError(apiError.message);
      }
    } finally {
      setSharing(false);
    }
  }

  async function handleRevoke(shareId: string) {
    setRevokingId(shareId);
    setError(null);
    setSuccess(null);

    try {
      await revokeDocumentShare(documentId, shareId);
      setSuccess('Access revoked.');
      await fetchShares();
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Share Document"
      className="max-w-lg"
      footer={
        <Button variant="secondary" onClick={onClose}>
          Done
        </Button>
      }
    >
      <p className="text-sm text-slate-600">
        Share{' '}
        {documentTitle ? (
          <span className="font-medium text-slate-800">{documentTitle}</span>
        ) : (
          'this document'
        )}{' '}
        with other registered users for collaborative review.
      </p>

      {error && !fieldErrors.email && (
        <div
          role="alert"
          className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-800"
        >
          {error}
        </div>
      )}

      {success && (
        <div
          role="status"
          className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-800"
        >
          {success}
        </div>
      )}

      <form onSubmit={handleShare} className="mt-4 space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="flex-1">
            <Input
              id="share-email"
              label="User email"
              type="email"
              placeholder="colleague@example.com"
              required
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                if (fieldErrors.email) {
                  setFieldErrors(({ email: _removed, ...rest }) => rest);
                }
              }}
              error={fieldErrors.email}
            />
          </div>
          <div className="w-full sm:w-32">
            <Select
              id="share-permission"
              label="Permission"
              value={permission}
              onChange={(e) => setPermission(e.target.value as SharePermission)}
            >
              <option value="view">View</option>
              <option value="review">Review</option>
            </Select>
          </div>
          <Button
            type="submit"
            className="w-full shrink-0 sm:w-auto"
            loading={sharing}
          >
            <UserPlus aria-hidden="true" className="mr-1.5 size-4" />
            Share
          </Button>
        </div>
      </form>

      <div className="mt-6 border-t border-slate-200 pt-4">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
          <Users aria-hidden="true" className="size-4" />
          People with access
        </h3>

        {loading ? (
          <div className="flex items-center justify-center py-6 text-slate-400">
            <Spinner label="Loading collaborators..." />
          </div>
        ) : shares.length === 0 ? (
          <p className="py-4 text-center text-xs text-slate-400">
            This document hasn’t been shared with anyone yet.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {shares.map((share) => (
              <li
                key={share.id}
                className="flex items-center justify-between py-2 text-sm"
              >
                <div className="min-w-0 pr-2">
                  <p className="truncate font-medium text-slate-900">
                    {share.shared_with_email}
                  </p>
                  {share.shared_with_name && (
                    <p className="text-xs text-slate-500">
                      {share.shared_with_name}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium border',
                      share.permission === 'review'
                        ? 'border-sky-200 bg-sky-50 text-sky-800'
                        : 'border-slate-200 bg-slate-50 text-slate-600'
                    )}
                  >
                    {share.permission === 'review' ? 'Can review' : 'View only'}
                  </span>
                  <button
                    type="button"
                    title="Remove access"
                    aria-label={`Remove access for ${share.shared_with_email}`}
                    onClick={() => handleRevoke(share.id)}
                    disabled={revokingId === share.id}
                    className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-red-600 disabled:opacity-50"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
