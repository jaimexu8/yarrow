'use client';

import { forwardRef, useState, type ComponentProps } from 'react';
import { Input } from './Input';

type PasswordInputProps = Omit<
  ComponentProps<typeof Input>,
  'type' | 'trailing'
>;

/** A password field with a Show/Hide toggle. Pasting is always allowed. */
export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  function PasswordInput(props, ref) {
    const [visible, setVisible] = useState(false);

    return (
      <Input
        ref={ref}
        type={visible ? 'text' : 'password'}
        trailing={
          <button
            type="button"
            onClick={() => setVisible((v) => !v)}
            aria-pressed={visible}
            aria-controls={props.id}
            className="rounded px-2 py-1 text-sm font-medium text-slate-700 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-900"
          >
            {visible ? 'Hide' : 'Show'}
            <span className="sr-only"> password</span>
          </button>
        }
        {...props}
      />
    );
  }
);
