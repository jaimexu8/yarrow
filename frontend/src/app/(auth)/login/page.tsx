'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useState, type FormEvent } from 'react';
import { VerifyEmailForm } from '@/components/auth/VerifyEmailForm';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { Spinner } from '@/components/ui/Spinner';
import { useAuth } from '@/context/AuthContext';
import { loginRequest, resendVerification } from '@/lib/auth';
import { toApiError } from '@/lib/errors';
import { safeNext } from '@/lib/redirect';

const NOT_VERIFIED = 'Email address not verified';

/** US-2 Login. */
function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { status, login } = useAuth();
  const next = safeNext(params.get('next'));
  const justVerified = params.get('verified') === '1';
  const justReset = params.get('reset') === '1';

  const [view, setView] = useState<'login' | 'verify'>('login');
  const [email, setEmail] = useState(params.get('email') ?? '');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [verifiedHere, setVerifiedHere] = useState(false);
  // The credentials of the sign-in attempt that turned out to be unverified,
  // frozen at submit time. The code step and the sign-in after it use only
  // these, so editing the fields mid-request cannot switch accounts.
  const [pending, setPending] = useState<{
    email: string;
    password: string;
  } | null>(null);

  // Already signed in (e.g. opened /login in a second tab): skip the form.
  useEffect(() => {
    if (status === 'authenticated') router.replace(next);
  }, [status, next, router]);

  async function signIn(credentials: { email: string; password: string }) {
    await login(await loginRequest(credentials.email, credentials.password));
    router.replace(next);
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    const sent = { email: email.trim().toLowerCase(), password };
    try {
      await signIn(sent);
    } catch (err) {
      const apiError = toApiError(err);
      if (apiError.status === 403 && apiError.message === NOT_VERIFIED) {
        // Right password, unverified address: send a fresh code and switch
        // to the code step instead of leaving the user at a dead end.
        await resendVerification(sent.email).catch(() => undefined);
        setPending(sent);
        setView('verify');
      } else if (apiError.status === 401) {
        setError('Incorrect email or password.');
      } else {
        setError(apiError.message);
      }
      setSubmitting(false);
    }
  }

  async function handleVerified() {
    if (!pending) return;
    try {
      await signIn(pending);
    } catch {
      // The address is verified either way, so the code form is now a dead
      // end (the backend rejects a second verification). Go back to sign in.
      setVerifiedHere(true);
      setError(null);
      setPassword('');
      setView('login');
    }
  }

  if (view === 'verify' && pending) {
    return (
      <VerifyEmailForm
        email={pending.email}
        onVerified={handleVerified}
        onBack={() => {
          setView('login');
          setPassword('');
        }}
        backLabel="Back to sign in"
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          Sign in to Yarrow
        </h1>
      </div>

      {(justVerified || verifiedHere) && !error && (
        <Alert tone="success">
          Your email is verified. Sign in to continue.
        </Alert>
      )}
      {justReset && !error && (
        <Alert tone="success">
          Your password has been changed. Sign in with your new password.
        </Alert>
      )}
      {error && <Alert>{error}</Alert>}

      <form onSubmit={handleSubmit} className="space-y-4">
        <Input
          id="email"
          label="Email"
          type="email"
          autoComplete="username"
          enterKeyHint="next"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          invalidMessage="Enter a valid email address, like you@example.com."
        />
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
        <div className="flex justify-end">
          <Link
            href={
              email
                ? `/forgot-password?email=${encodeURIComponent(email.trim())}`
                : '/forgot-password'
            }
            className="rounded text-sm font-medium text-slate-900 underline underline-offset-4 hover:text-slate-600"
          >
            Forgot password?
          </Link>
        </div>
        <Button type="submit" loading={submitting}>
          Sign in
        </Button>
      </form>

      <p className="text-center text-sm text-slate-600">
        New to Yarrow?{' '}
        <Link
          href="/register"
          className="font-medium text-slate-900 underline underline-offset-4"
        >
          Create an account
        </Link>
      </p>
    </div>
  );
}

export default function LoginPage() {
  // useSearchParams needs a Suspense boundary for Next to prerender the page.
  return (
    <Suspense
      fallback={
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
