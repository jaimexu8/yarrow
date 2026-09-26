'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { resendVerification, verifyEmail } from '@/lib/auth';
import { toApiError } from '@/lib/errors';

/** Matches the backend's VERIFICATION_RESEND_COOLDOWN_SECONDS. */
const RESEND_COOLDOWN_SECONDS = 60;

type VerifyEmailFormProps = {
  email: string;
  /** Called after the backend accepts the code. */
  onVerified: () => Promise<void> | void;
  /** Leave this step, e.g. to fix a mistyped email address. */
  onBack?: () => void;
  backLabel?: string;
};

/**
 * Step two of sign-up (US-1): enter the 6-digit code that was emailed.
 * Used after registering and when an unverified account tries to sign in.
 */
export function VerifyEmailForm({
  email,
  onVerified,
  onBack,
  backLabel = 'Use a different email',
}: VerifyEmailFormProps) {
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [resending, setResending] = useState(false);
  // A code was just sent, so resending is locked for the cooldown.
  const [cooldown, setCooldown] = useState(RESEND_COOLDOWN_SECONDS);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    // The view just changed under the user, so move focus to its heading.
    headingRef.current?.focus();
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setSubmitting(true);
    try {
      await verifyEmail(email, code);
    } catch (err) {
      const apiError = toApiError(err);
      setError(
        apiError.status === 400
          ? 'That code is wrong or has expired. Check the email, or request a new code.'
          : (apiError.fields.code ?? apiError.message)
      );
      setSubmitting(false);
      return;
    }
    // Kept apart from the verify call: once the code is accepted, whatever
    // happens next is the caller's to handle, and must never be reported as
    // a bad code (the account can't be verified twice).
    await onVerified();
  }

  async function handleResend() {
    setError(null);
    setNotice(null);
    setResending(true);
    try {
      await resendVerification(email);
      // The backend answers the same way whether or not it sent a code
      // (so it cannot be used to probe accounts), and the wording here
      // stays equally honest.
      setNotice(`If a new code can be sent, it is on its way to ${email}.`);
      setCode('');
      setCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setResending(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold tracking-tight text-slate-900 focus:outline-none"
        >
          Check your email
        </h1>
        <p className="text-sm text-slate-600">
          We sent a 6-digit code to{' '}
          <span className="font-medium text-slate-900">{email}</span>. It
          expires in 15 minutes.
        </p>
      </div>

      {error && <Alert>{error}</Alert>}
      {notice && <Alert tone="info">{notice}</Alert>}

      <form onSubmit={handleSubmit} className="space-y-4">
        <Input
          id="one-time-code"
          label="Verification code"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          enterKeyHint="done"
          pattern="[0-9]{6}"
          required
          value={code}
          // Accept pasted codes like "123 456" by keeping only the digits.
          // No maxLength on purpose: the browser would cut the raw paste to
          // "123 45" before this handler could strip the space.
          onChange={(e) =>
            setCode(e.target.value.replace(/\D/g, '').slice(0, 6))
          }
          invalidMessage="Enter the 6-digit code from the email."
          className="font-mono text-lg tracking-[0.4em]"
        />
        <Button type="submit" loading={submitting}>
          Verify email
        </Button>
      </form>

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-600">
        <Button
          variant="link"
          onClick={handleResend}
          disabled={cooldown > 0}
          loading={resending}
        >
          {cooldown > 0 ? `Resend code in ${cooldown}s` : 'Resend code'}
        </Button>
        {onBack && (
          <Button variant="link" onClick={onBack}>
            {backLabel}
          </Button>
        )}
      </div>
      <p className="text-xs text-slate-500">
        A code stops working after 5 wrong tries. Request a new one if that
        happens.
      </p>
    </div>
  );
}
