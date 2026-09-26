'use client';

import {
  forwardRef,
  useState,
  type FormEvent,
  type InputHTMLAttributes,
  type ReactNode,
} from 'react';
import { cn } from '@/lib/cn';

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  id: string;
  label: string;
  /** Guidance shown above the input, so autofill popovers never cover it. */
  hint?: ReactNode;
  /** Shown once the browser considers the value invalid after interaction. */
  invalidMessage?: string;
  /** An error from the server. Always shown while set. */
  error?: string;
  /** Rendered inside the right edge of the input (e.g. a show-password toggle). */
  trailing?: ReactNode;
};

/**
 * Whether the browser has flagged the input as invalid *after* the user
 * interacted with it (blurred it, or tried to submit). Using :user-invalid
 * means errors never appear while someone is still typing.
 */
function isUserInvalid(input: HTMLInputElement): boolean {
  try {
    return input.matches(':user-invalid');
  } catch {
    return !input.checkValidity();
  }
}

type InvalidKind = 'missing' | 'invalid' | null;

function invalidKind(input: HTMLInputElement, force = false): InvalidKind {
  if (!force && !isUserInvalid(input)) return null;
  if (input.validity.valid) return null;
  return input.validity.valueMissing ? 'missing' : 'invalid';
}

/**
 * A labeled text field. The visual error state and aria-invalid come from
 * the same piece of state, so screen reader users hear an error exactly when
 * sighted users see one.
 */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    id,
    label,
    hint,
    invalidMessage,
    error,
    trailing,
    className,
    onBlur,
    onInput,
    onInvalid,
    ...props
  },
  ref
) {
  const [invalid, setInvalid] = useState<InvalidKind>(null);
  // An empty required field gets "X is required." rather than a format hint.
  const clientMessage =
    invalid === 'missing'
      ? `${label.replace(/\s*\(optional\)$/i, '')} is required.`
      : invalid === 'invalid'
        ? invalidMessage
        : undefined;
  const message = error ?? clientMessage;
  const showError = Boolean(message);

  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = `${id}-error`;

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-slate-900">
        {label}
      </label>
      {hint && (
        <p id={hintId} className="text-sm text-slate-600">
          {hint}
        </p>
      )}
      <div className="relative">
        <input
          ref={ref}
          id={id}
          name={id}
          aria-describedby={hintId}
          aria-invalid={showError || undefined}
          aria-errormessage={showError ? errorId : undefined}
          className={cn(
            'block w-full rounded-lg border bg-white px-3 py-2.5 text-slate-900 shadow-sm',
            'placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-offset-0',
            showError
              ? 'border-red-600 focus:border-red-600 focus:ring-red-200'
              : 'border-slate-300 focus:border-slate-900 focus:ring-slate-200',
            trailing ? 'pr-20' : undefined,
            className
          )}
          onBlur={(e) => {
            setInvalid(invalidKind(e.currentTarget));
            onBlur?.(e);
          }}
          onInput={(e: FormEvent<HTMLInputElement>) => {
            // Once an error is showing, update it as the value changes and
            // clear it the moment the value is fixed. Checked on the next
            // frame so a parent that reformats the value (e.g. strips spaces
            // from a pasted code) is validated after its change lands.
            if (invalid) {
              const el = e.currentTarget;
              requestAnimationFrame(() => setInvalid(invalidKind(el, true)));
            }
            onInput?.(e);
          }}
          onInvalid={(e) => {
            // Fired for each invalid field when the form is submitted. Show
            // our own message instead of the browser's tooltip, and focus the
            // first invalid field so keyboard users land on the problem.
            e.preventDefault();
            setInvalid(invalidKind(e.currentTarget, true));
            const first = e.currentTarget.form?.querySelector(':invalid');
            if (first === e.currentTarget) e.currentTarget.focus();
            onInvalid?.(e);
          }}
          {...props}
        />
        {trailing && (
          <div className="absolute inset-y-0 right-0 flex items-center pr-2">
            {trailing}
          </div>
        )}
      </div>
      {showError && (
        <p id={errorId} className="flex gap-1.5 text-sm text-red-700">
          <span aria-hidden="true">!</span>
          {message}
        </p>
      )}
    </div>
  );
});
