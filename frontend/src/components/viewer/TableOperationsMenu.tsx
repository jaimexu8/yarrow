'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '../ui/Button';
import { toApiError } from '@/lib/errors';
import {
  mergeConsecutiveTables,
  splitConsecutiveTables,
  type TableMutationResult,
} from '@/lib/tableOperations';
import { cn } from '@/lib/cn';
import { useViewer } from './ViewerContext';

type TableOperation = 'merge' | 'split';

interface TableOperationsDropdownProps {
  onDone: () => void;
}

interface OperationMessage {
  text: string;
  error: boolean;
}

export function TableOperationsDropdown({
  onDone,
}: TableOperationsDropdownProps) {
  const { document: viewerDocument } = useViewer();

  const [open, setOpen] = useState<boolean>(false);
  const [running, setRunning] = useState<TableOperation | null>(null);
  const [message, setMessage] = useState<OperationMessage | null>(null);

  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const handleOutsideClick = (event: MouseEvent): void => {
      if (
        menuRef.current &&
        event.target instanceof Node &&
        !menuRef.current.contains(event.target)
      ) {
        setOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };

    window.addEventListener('mousedown', handleOutsideClick);
    window.addEventListener('keydown', handleKeyDown);

    return () => {
      window.removeEventListener('mousedown', handleOutsideClick);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  if (viewerDocument.reprocess?.scope !== 'all') {
    return null;
  }

  const handleOperation = async (operation: TableOperation): Promise<void> => {
    if (running !== null) return;

    const run: (documentId: string) => Promise<TableMutationResult> =
      operation === 'merge' ? mergeConsecutiveTables : splitConsecutiveTables;

    const nothingToDo: string =
      operation === 'merge' ? 'No tables to merge.' : 'No tables to split.';

    setRunning(operation);
    setMessage(null);
    setOpen(false);

    try {
      const result: TableMutationResult = await run(viewerDocument.id);

      if (result.changed) {
        onDone();
      } else {
        setMessage({
          text: nothingToDo,
          error: false,
        });
      }
    } catch (err: unknown) {
      setMessage({
        text: toApiError(err).message,
        error: true,
      });
    } finally {
      setRunning(null);
    }
  };

  return (
    <div className="flex flex-col items-start gap-2">
      <div className="relative" ref={menuRef}>
        <Button
          variant="secondary"
          className="w-auto px-3 py-1.5"
          loading={running !== null}
          onClick={() => {
            if (running === null) {
              setOpen((previous: boolean) => !previous);
            }
          }}
        >
          Tables
          <span
            aria-hidden="true"
            className={cn(
              'ml-2 inline-block transition-transform',
              open && 'rotate-180'
            )}
          >
            ▾
          </span>
        </Button>

        {open && (
          <div
            role="menu"
            aria-label="Table operations"
            className="absolute right-0 top-full z-50 mt-2 min-w-48 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg"
          >
            <button
              type="button"
              role="menuitem"
              disabled={running !== null}
              onClick={() => void handleOperation('merge')}
              className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span aria-hidden="true">⇉</span>
              <span>Merge tables</span>
            </button>

            <button
              type="button"
              role="menuitem"
              disabled={running !== null}
              onClick={() => void handleOperation('split')}
              className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span aria-hidden="true">⇥</span>
              <span>Split tables</span>
            </button>
          </div>
        )}
      </div>

      {message && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setMessage(null);
            }
          }}
        >
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="table-alert-title"
            aria-describedby="table-alert-description"
            className="w-full max-w-md rounded-xl bg-white p-6 shadow-2xl"
          >
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <p
                  id="table-alert-description"
                  className="mt-2 text-sm leading-6 text-slate-600"
                >
                  {message.text}
                </p>
              </div>
            </div>

            <div className="mt-6 flex justify-end">
              <Button
                variant="primary"
                className="px-5 py-2"
                onClick={() => setMessage(null)}
              >
                OK
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
