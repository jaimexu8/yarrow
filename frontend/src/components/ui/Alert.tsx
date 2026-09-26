import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

type Tone = 'error' | 'success' | 'info';

const TONES: Record<Tone, string> = {
  error: 'border-red-200 bg-red-50 text-red-800',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  info: 'border-slate-200 bg-slate-50 text-slate-700',
};

/**
 * A form-level message. Errors use role="alert" so they are announced as
 * soon as they appear; other tones are polite status updates.
 */
export function Alert({
  tone = 'error',
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={cn(
        'rounded-lg border px-3 py-2.5 text-sm',
        TONES[tone],
        className
      )}
    >
      {children}
    </div>
  );
}
