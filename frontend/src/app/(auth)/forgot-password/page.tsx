'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Spinner } from '@/components/ui/Spinner';
import { requestPasswordReset } from '@/lib/auth';
import { toApiError } from '@/lib/errors';

/** US-67 step 1: ask for a reset link by email. */
function ForgotPasswordForm() {
  const params = useSearchParams();
  const [email, setEmail] = useState(params.get('email') ?? '');
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setFieldError(undefined);
    setSubmitting(true);
    const address = email.trim();
    try {
      await requestPasswordReset(address);
      setSentTo(address);
    } catch (err) {
      const apiError = toApiError(err);
      if (apiError.fields.email) setFieldError(apiError.fields.email);
      else setError(apiError.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (sentTo) {
    return (
      <div className="space-y-6">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Check your email
          </h1>
          {/* Worded so it never confirms whether the address has an account,
              matching the server, which answers the same way either way. */}
          <p className="text-sm text-slate-600">
            If there is a Yarrow account for{' '}
            <span className="font-medium text-slate-900">{sentTo}</span>, we
            sent it a link to reset the password. The link works once and
            expires in 30 minutes.
          </p>
        </div>
        <p className="text-sm text-slate-600">
          No email after a few minutes? Check your spam folder, or{' '}
          <button
            type="button"
            onClick={() => setSentTo(null)}
            className="font-medium text-slate-900 underline underline-offset-4"
          >
            try again
          </button>
          .
        </p>
        <Link
          href="/login"
          className="block text-center text-sm font-medium text-slate-900 underline underline-offset-4"
        >
          Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          Reset your password
        </h1>
        <p className="text-sm text-slate-600">
          Enter the email you signed up with and we will send you a link to
          choose a new password.
        </p>
      </div>

      {error && <Alert>{error}</Alert>}

      <form onSubmit={handleSubmit} className="space-y-4">
        <Input
          id="email"
          label="Email"
          type="email"
          autoComplete="username"
          enterKeyHint="send"
          required
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            setFieldError(undefined);
          }}
          invalidMessage="Enter a valid email address, like you@example.com."
          error={fieldError}
        />
        <Button type="submit" loading={submitting}>
          Send reset link
        </Button>
      </form>

      <p className="text-center text-sm text-slate-600">
        Remembered it?{' '}
        <Link
          href="/login"
          className="font-medium text-slate-900 underline underline-offset-4"
        >
          Sign in
        </Link>
      </p>
    </div>
  );
}

export default function ForgotPasswordPage() {
  // useSearchParams needs a Suspense boundary for Next to prerender the page.
  return (
    <Suspense
      fallback={
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      }
    >
      <ForgotPasswordForm />
    </Suspense>
  );
}
