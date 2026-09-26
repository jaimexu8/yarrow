'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { Spinner } from '@/components/ui/Spinner';
import { useAuth } from '@/context/AuthContext';
import { confirmPasswordReset } from '@/lib/auth';
import { toApiError } from '@/lib/errors';

type LinkState = 'reading' | 'ok' | 'missing';

/**
 * US-67 step 2: choose a new password from the emailed link.
 *
 * The link looks like /reset-password#token=... The token is after "#" so
 * the browser never sends it to any server, including in the Referer header.
 */
export default function ResetPasswordPage() {
  const router = useRouter();
  const { clearSession } = useAuth();
  const [token, setToken] = useState('');
  const [linkState, setLinkState] = useState<LinkState>('reading');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [linkDead, setLinkDead] = useState(false);
  const [fieldError, setFieldError] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    // The hash only exists in the browser, so it is read after mount.
    function readToken() {
      const found = new URLSearchParams(window.location.hash.slice(1)).get(
        'token'
      );
      if (found) {
        setToken(found);
        setLinkState('ok');
        setLinkDead(false);
        setError(null);
        // Take the token out of the address bar and history, so it isn't
        // left behind for the next person using this browser. This goes
        // through Next's router: editing window.history directly gets
        // overwritten, because the router re-writes the URL it knows about.
        router.replace('/reset-password', { scroll: false });
      } else {
        // Keep 'ok' if a token was already read. This can run twice (React
        // does that on purpose in development), and by the second run the
        // line above has already removed the token from the address bar.
        setLinkState((current) => (current === 'ok' ? 'ok' : 'missing'));
      }
    }

    readToken();
    // Pasting a second reset link into a tab already on this page changes
    // only the "#..." part, which doesn't reload the page. Pick it up anyway.
    window.addEventListener('hashchange', readToken);
    return () => window.removeEventListener('hashchange', readToken);
  }, [router]);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setFieldError(undefined);
    setSubmitting(true);
    try {
      await confirmPasswordReset(token, password);
      // The reset ended every session on the server, including one this
      // browser may hold. Forget it here too; otherwise the login page would
      // treat the dead session as signed in and skip straight past itself.
      clearSession();
      router.replace('/login?reset=1');
    } catch (err) {
      const apiError = toApiError(err);
      if (apiError.status === 400) {
        setLinkDead(true);
      } else if (apiError.fields.new_password) {
        setFieldError(apiError.fields.new_password);
      } else {
        setError(apiError.message);
      }
      setSubmitting(false);
    }
  }

  if (linkState === 'reading') {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  if (linkState === 'missing' || linkDead) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          This link can&apos;t be used
        </h1>
        <Alert>
          {linkDead
            ? 'This reset link has expired, was already used, or was replaced by a newer one.'
            : 'This page needs the full link from your reset email. Try opening the link again, or copy the whole link into your browser.'}
        </Alert>
        <Link
          href="/forgot-password"
          className="block w-full rounded-lg bg-slate-900 px-4 py-2.5 text-center text-sm font-medium text-white hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          Request a new link
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          Choose a new password
        </h1>
        <p className="text-sm text-slate-600">
          After this, sign in with your new password.
        </p>
      </div>

      {error && <Alert>{error}</Alert>}

      <form onSubmit={handleSubmit} className="space-y-4">
        <PasswordInput
          id="new-password"
          label="New password"
          hint="At least 8 characters."
          autoComplete="new-password"
          enterKeyHint="done"
          required
          minLength={8}
          maxLength={72}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            setFieldError(undefined);
          }}
          invalidMessage="Password must be at least 8 characters."
          error={fieldError}
        />
        <Button type="submit" loading={submitting}>
          Change password
        </Button>
      </form>
    </div>
  );
}
