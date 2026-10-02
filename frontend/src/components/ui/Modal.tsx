'use client';

import { useEffect, useId, useRef, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

type ModalProps = {
  open: boolean;
  /** Called for Escape and backdrop clicks; the parent decides to close. */
  onClose: () => void;
  title: string;
  children: ReactNode;
  /** Buttons along the bottom, right-aligned. */
  footer?: ReactNode;
  /** False while something is in flight, so it cannot be dismissed midway. */
  dismissible?: boolean;
  className?: string;
};

/**
 * A modal dialog built on the native <dialog> element, which keeps focus
 * inside it, makes the rest of the page inert, and returns focus to where it
 * was when it closes. For destructive confirmations that should be the safe
 * choice.
 */
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  dismissible = true,
  className,
}: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      dialog.querySelector<HTMLElement>('[data-autofocus]')?.focus();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (dismissible) onClose();
      }}
      // A click whose target is the <dialog> itself landed on the backdrop.
      onClick={(event) => {
        if (event.target === event.currentTarget && dismissible) onClose();
      }}
      className={cn(
        'w-[calc(100%-2rem)] max-w-md rounded-2xl border border-slate-200 bg-white p-0 text-slate-900 shadow-xl backdrop:bg-slate-900/40',
        // The dialog shows above the page but still inherits text styles
        // from wherever it sits in the DOM
        'whitespace-normal text-left font-normal',
        className
      )}
    >
      {open && (
        <div className="p-6">
          <h2 id={titleId} className="text-lg font-semibold tracking-tight">
            {title}
          </h2>
          <div className="mt-2 text-sm text-slate-600">{children}</div>
          {footer && (
            <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              {footer}
            </div>
          )}
        </div>
      )}
    </dialog>
  );
}