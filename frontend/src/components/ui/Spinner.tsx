import { cn } from '@/lib/cn';

type SpinnerProps = {
  className?: string;
  /** Announced to screen readers. Pass null when a parent already says it. */
  label?: string | null;
};

export function Spinner({ className, label = 'Loading' }: SpinnerProps) {
  return (
    <span
      className="inline-flex items-center"
      role={label ? 'status' : undefined}
    >
      <svg
        className={cn('size-4 animate-spin', className)}
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <circle
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeWidth="3"
          className="opacity-25"
        />
        <path
          d="M22 12a10 10 0 0 0-10-10"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
        />
      </svg>
      {label && <span className="sr-only">{label}</span>}
    </span>
  );
}
