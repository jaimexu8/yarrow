import { isAxiosError } from 'axios';

/** One entry of FastAPI's 422 validation error list. */
type ValidationIssue = { loc?: (string | number)[]; msg?: string };

export type ApiError = {
  status: number | null;
  /** A sentence that can be shown to the user as is. */
  message: string;
  /** Per-field messages from a 422, keyed by field name (e.g. "password"). */
  fields: Record<string, string>;
};

const FIELD_LABELS: Record<string, string> = {
  email: 'Email',
  password: 'Password',
  new_password: 'New password',
  name: 'Name',
  code: 'Code',
};

const PASSWORD_FIELDS = new Set(['password', 'new_password']);

function friendlyIssue(field: string, msg: string): string {
  if (PASSWORD_FIELDS.has(field) && /at least 8/i.test(msg)) {
    return 'Password must be at least 8 characters.';
  }
  if (PASSWORD_FIELDS.has(field) && /72 bytes/i.test(msg)) {
    return 'Password is too long.';
  }
  if (field === 'email') return 'Enter a valid email address.';
  if (field === 'code') return 'Enter the 6-digit code from the email.';
  if (/field required/i.test(msg)) {
    return `${FIELD_LABELS[field] ?? 'This field'} is required.`;
  }
  // Pydantic prefixes custom validator messages with "Value error, ".
  const cleaned = msg.replace(/^Value error, /i, '');
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

/**
 * Turn anything thrown by an API call into text for the UI. Never surfaces a
 * stack trace or raw server payload.
 */
export function toApiError(error: unknown): ApiError {
  if (!isAxiosError(error)) {
    return {
      status: null,
      message: 'Something went wrong. Please try again.',
      fields: {},
    };
  }
  if (!error.response) {
    return {
      status: null,
      message:
        'Could not reach the server. Check your connection and try again.',
      fields: {},
    };
  }

  const { status, data } = error.response;
  const detail = (data as { detail?: unknown } | undefined)?.detail;

  if (status === 422 && Array.isArray(detail)) {
    const fields: Record<string, string> = {};
    for (const issue of detail as ValidationIssue[]) {
      const field = String(issue.loc?.[issue.loc.length - 1] ?? '');
      if (field && !fields[field]) {
        fields[field] = friendlyIssue(field, issue.msg ?? '');
      }
    }
    return {
      status,
      message: 'Please fix the highlighted fields.',
      fields,
    };
  }

  if (typeof detail === 'string') {
    return { status, message: detail, fields: {} };
  }

  if (status >= 500) {
    return {
      status,
      message: 'The server had a problem. Please try again in a moment.',
      fields: {},
    };
  }
  return {
    status,
    message: 'Something went wrong. Please try again.',
    fields: {},
  };
}
