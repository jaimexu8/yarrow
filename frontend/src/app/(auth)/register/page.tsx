'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState, type FormEvent } from 'react';
import { VerifyEmailForm } from '@/components/auth/VerifyEmailForm';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { useAuth } from '@/context/AuthContext';
import { loginRequest, registerAccount, resendVerification } from '@/lib/auth';
import { toApiError } from '@/lib/errors';
import { DEFAULT_AFTER_LOGIN } from '@/lib/redirect';

type Step = 'details' | 'verify';

/**
 * US-1 Create Account: details, then the emailed verification code. After a
 * successful verification the user is signed in with the password they just
 * chose, since it is still in memory on this page.
 */
export default function RegisterPage() {
  const router = useRouter();
  const { status, login } = useAuth();

  const [step, setStep] = useState<Step>('details');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [emailTaken, setEmailTaken] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // The details the account was actually created with, frozen when the
  // request was sent. The verify step and auto sign-in use only these, so
  // editing a field while the request is in flight cannot redirect them.
  const [submitted, setSubmitted] = useState<{
    email: string;
    password: string;
  } | null>(null);

  useEffect(() => {
    if (status === 'authenticated') router.replace(DEFAULT_AFTER_LOGIN);
  }, [status, router]);

  function clearFieldError(field: string) {
    if (fieldErrors[field]) {
      setFieldErrors(({ [field]: _removed, ...rest }) => rest);
    }
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFormError(null);
    setFieldErrors({});
    setEmailTaken(false);
    setSubmitting(true);
    const sent = { email, password };
    try {
      const user = await registerAccount({ name, ...sent });
      // The server's copy of the email is canonical (lowercased).
      setSubmitted({ email: user.email, password: sent.password });
      setStep('verify');
    } catch (err) {
      const apiError = toApiError(err);
      if (apiError.status === 409) {
        setEmailTaken(true);
      } else if (Object.keys(apiError.fields).length > 0) {
        setFieldErrors(apiError.fields);
      } else {
        setFormError(apiError.message);
      }
    } finally {
      setSubmitting(false);
    }
  }

  /** An account exists but may never have been verified: send a new code. */
  async function continueVerification() {
    const sent = { email: email.trim().toLowerCase(), password };
    setEmailTaken(false);
    try {
      await resendVerification(sent.email);
    } catch {
      // The verify step has its own resend button, so carry on regardless.
    }
    setSubmitted(sent);
    setStep('verify');
  }

  async function handleVerified() {
    if (!submitted) return;
    try {
      await login(await loginRequest(submitted.email, submitted.password));
      router.replace(DEFAULT_AFTER_LOGIN);
    } catch {
      // Verified, but automatic sign-in failed (e.g. "Finish verifying it"
      // with a different password): send them to sign in by hand.
      const address = encodeURIComponent(submitted.email);
      router.replace(`/login?verified=1&email=${address}`);
    }
  }

  if (step === 'verify' && submitted) {
    return (
      <VerifyEmailForm
        email={submitted.email}
        onVerified={handleVerified}
        onBack={() => setStep('details')}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          Create your account
        </h1>
        <p className="text-sm text-slate-600">
          Step 1 of 2. We will email you a code to confirm your address.
        </p>
      </div>

      {formError && <Alert>{formError}</Alert>}
      {emailTaken && (
        <Alert>
          <p>An account with that email already exists.</p>
          <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            <Link
              href={`/login?email=${encodeURIComponent(email)}`}
              className="font-medium underline underline-offset-4"
            >
              Sign in instead
            </Link>
            <button
              type="button"
              onClick={continueVerification}
              className="font-medium underline underline-offset-4"
            >
              Finish verifying it
            </button>
          </p>
        </Alert>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <Input
          id="name"
          label="Name (optional)"
          type="text"
          autoComplete="name"
          enterKeyHint="next"
          value={name}
          onChange={(e) => setName(e.target.value)}
          error={fieldErrors.name}
        />
        <Input
          id="email"
          label="Email"
          type="email"
          autoComplete="username"
          enterKeyHint="next"
          required
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            clearFieldError('email');
            setEmailTaken(false);
          }}
          invalidMessage="Enter a valid email address, like you@example.com."
          error={fieldErrors.email}
        />
        <PasswordInput
          id="new-password"
          label="Password"
          hint="At least 8 characters."
          autoComplete="new-password"
          enterKeyHint="done"
          required
          minLength={8}
          maxLength={72}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            clearFieldError('password');
          }}
          invalidMessage="Password must be at least 8 characters."
          error={fieldErrors.password}
        />
        <Button type="submit" loading={submitting}>
          Create account
        </Button>
      </form>

      <p className="text-center text-sm text-slate-600">
        Already have an account?{' '}
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
