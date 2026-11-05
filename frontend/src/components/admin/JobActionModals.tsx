'use client';

import { useId, useState } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { cn } from '@/lib/cn';
import { forceCancelJob, retryJob, type AdminJob } from '@/lib/admin';
import { toApiError } from '@/lib/errors';

export type JobAction = { kind: 'cancel' | 'retry'; job: AdminJob };

const REASON_MAX_LENGTH = 500;

export function JobActionButtons({
  job,
  onAction,
}: {
  job: AdminJob;
  onAction: (action: JobAction) => void;
}) {
  if (!job.can_cancel && !job.can_retry) {
    return <span className="text-slate-400">—</span>;
  }
  return (
    <span className="flex justify-end gap-2">
      {job.can_cancel && (
        <Button
          variant="secondary"
          className="w-auto whitespace-nowrap px-3 py-1.5 text-red-700 hover:bg-red-50"
          aria-label={`Force cancel ${job.filename}`}
          onClick={() => onAction({ kind: 'cancel', job })}
        >
          Force cancel
        </Button>
      )}
      {job.can_retry && (
        <Button
          variant="secondary"
          className="w-auto whitespace-nowrap px-3 py-1.5"
          aria-label={`Retry ${job.filename}`}
          onClick={() => onAction({ kind: 'retry', job })}
        >
          Retry
        </Button>
      )}
    </span>
  );
}

export function StuckBadge() {
  return (
    <span className="ml-2 inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs font-medium text-orange-800">
      Stuck
    </span>
  );
}

export function JobActionModal({
  action,
  onClose,
  onDone,
}: {
  action: JobAction | null;
  onClose: () => void;
  onDone: () => Promise<void> | void;
}) {
  const reasonId = useId();
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const job = action?.job;
  const isCancel = action?.kind === 'cancel';

  async function confirm() {
    if (!action) return;
    setPending(true);
    setError(null);
    try {
      if (action.kind === 'cancel') {
        await forceCancelJob(action.job.id, reason);
      } else {
        await retryJob(action.job.id);
      }
      onClose();
      await onDone();
    } catch (err) {
      const apiError = toApiError(err);
      setError(apiError.message);
      if (apiError.status === 409) await onDone();
    } finally {
      setPending(false);
    }
  }

  return (
    <Modal
      open={action !== null}
      onClose={() => {
        if (!pending) onClose();
      }}
      dismissible={!pending}
      title={isCancel ? 'Force-cancel this job?' : 'Retry this job?'}
      footer={
        <>
          <Button
            variant="secondary"
            className="sm:w-auto"
            onClick={onClose}
            disabled={pending}
            data-autofocus
          >
            Cancel
          </Button>
          <Button
            variant={isCancel ? 'danger' : 'primary'}
            className="sm:w-auto"
            onClick={confirm}
            loading={pending}
          >
            {isCancel ? 'Force cancel' : 'Retry'}
          </Button>
        </>
      }
    >
      {job && isCancel && (
        <>
          <p className="break-words">
            Processing of{' '}
            <span className="font-medium text-slate-900">{job.filename}</span>{' '}
            for{' '}
            <span className="font-medium text-slate-900">
              {job.owner_email}
            </span>{' '}
            stops now and its results are discarded.
          </p>
          <div className="mt-4 space-y-1.5">
            <label
              htmlFor={reasonId}
              className="block text-sm font-medium text-slate-900"
            >
              Reason (optional)
            </label>
            <textarea
              id={reasonId}
              value={reason}
              maxLength={REASON_MAX_LENGTH}
              rows={3}
              disabled={pending}
              onChange={(event) => setReason(event.target.value)}
              className={cn(
                'block w-full resize-y rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-900 shadow-sm',
                'placeholder:text-slate-400 focus:border-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-200 focus:ring-offset-0',
                'disabled:bg-slate-50 disabled:text-slate-500'
              )}
            />
            <p className="text-right text-xs tabular-nums text-slate-500">
              {reason.length}/{REASON_MAX_LENGTH}
            </p>
          </div>
        </>
      )}
      {job && !isCancel && (
        <p className="break-words">
          A new job will be queued for{' '}
          <span className="font-medium text-slate-900">{job.filename}</span>.
          The owner does not need to upload it again.
        </p>
      )}
      {error && (
        <Alert className="mt-4" tone="error">
          {error}
        </Alert>
      )}
    </Modal>
  );
}
