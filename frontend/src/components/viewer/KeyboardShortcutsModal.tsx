'use client';

import { Fragment, useEffect, useLayoutEffect, useRef } from 'react';
import { Modal } from '@/components/ui/Modal';
import { VIEWER_SHORTCUTS } from '@/lib/viewerShortcuts';

function useRestoreFocus(open: boolean) {
  const previous = useRef<HTMLElement | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const active = window.document.activeElement;
    previous.current = active instanceof HTMLElement ? active : null;
  }, [open]);

  useEffect(() => {
    if (open) return;
    const element = previous.current;
    previous.current = null;
    if (element?.isConnected) element.focus();
  }, [open]);
}

function ShortcutKeys({ label }: { label: string }) {
  const keys = label.split(' or ');
  return (
    <>
      {keys.map((key, index) => (
        <Fragment key={key}>
          {index > 0 && <span className="text-xs text-slate-400">or</span>}
          <kbd className="inline-flex min-w-7 items-center justify-center rounded-md border border-b-2 border-slate-300 bg-slate-50 px-1.5 py-0.5 font-sans text-xs font-medium text-slate-700">
            {key}
          </kbd>
        </Fragment>
      ))}
    </>
  );
}

export function KeyboardShortcutsModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  useRestoreFocus(open);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Keyboard shortcuts"
      footer={
        <button
          type="button"
          data-autofocus
          onClick={onClose}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          Close
        </button>
      }
    >
      <dl className="mt-3 divide-y divide-slate-100">
        {VIEWER_SHORTCUTS.map((shortcut) => (
          <div
            key={shortcut.action}
            className="flex items-center justify-between gap-4 py-2"
          >
            <dt className="text-slate-700">{shortcut.description}</dt>
            <dd className="flex shrink-0 items-center gap-1.5">
              <ShortcutKeys label={shortcut.label} />
            </dd>
          </div>
        ))}
      </dl>
    </Modal>
  );
}
