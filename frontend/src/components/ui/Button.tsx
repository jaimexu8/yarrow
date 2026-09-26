import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';
import { Spinner } from './Spinner';

type Variant = 'primary' | 'secondary' | 'link';

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  /** Disables the button and shows a spinner beside the label. */
  loading?: boolean;
};

const VARIANTS: Record<Variant, string> = {
  primary:
    'w-full justify-center rounded-lg bg-slate-900 px-4 py-2.5 text-white hover:bg-slate-800 disabled:bg-slate-400',
  secondary:
    'w-full justify-center rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-slate-900 hover:bg-slate-50 disabled:text-slate-400',
  link: 'rounded text-slate-900 underline underline-offset-4 hover:text-slate-600 disabled:text-slate-400 disabled:no-underline',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = 'primary',
      loading = false,
      disabled,
      className,
      children,
      type = 'button',
      ...props
    },
    ref
  ) {
    return (
      <button
        ref={ref}
        type={type}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        className={cn(
          'inline-flex items-center gap-2 text-sm font-medium transition-colors',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900',
          'disabled:cursor-not-allowed',
          VARIANTS[variant],
          className
        )}
        {...props}
      >
        {loading && <Spinner label={null} />}
        {children}
      </button>
    );
  }
);
