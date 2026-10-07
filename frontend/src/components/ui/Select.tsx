'use client';

import { forwardRef, type SelectHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  id: string;
  label: string;
  /** Rendered for screen readers only, when a visible heading next to the
   * field already names it. */
  hideLabel?: boolean;
};

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  function Select(
    { id, label, hideLabel = false, className, children, ...props },
    ref
  ) {
    return (
      <div className="space-y-1.5">
        <label
          htmlFor={id}
          className={cn(
            'block text-sm font-medium text-slate-900',
            hideLabel && 'sr-only'
          )}
        >
          {label}
        </label>
        <select
          ref={ref}
          id={id}
          name={id}
          className={cn(
            'block w-full rounded-lg border bg-white px-3 py-2.5 text-slate-900 shadow-sm',
            'focus:outline-none focus:ring-2 focus:ring-offset-0',
            'border-slate-300 focus:border-slate-900 focus:ring-slate-200',
            className
          )}
          {...props}
        >
          {children}
        </select>
      </div>
    );
  }
);
