'use client';

import { useEffect, useRef, useState } from 'react';
import { FileDown } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { toApiError } from '@/lib/errors';
import { exportDocument, type ExportFormat } from '@/lib/export';
import { cn } from '@/lib/cn';
import { useViewer } from './ViewerContext';

const OPTIONS: { format: ExportFormat; label: string }[] = [
  { format: 'markdown', label: 'Markdown (.md)' },
  { format: 'text', label: 'Plain text (.txt)' },
  { format: 'json', label: 'Structured JSON (.json)' },
];

/**
 * The Export button in the viewer header. Offers the extracted content as
 * Markdown or plain text. Disabled until the document has finished
 * processing, which is also what the export endpoint requires.
 */
export function ExportMenu() {
  const { document } = useViewer();

  const [open, setOpen] = useState(false);
  const [downloading, setDownloading] = useState<ExportFormat | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const completed = document.status === 'completed';

  async function handleExport(format: ExportFormat): Promise<void> {
    if (downloading !== null) return;

    setDownloading(format);
    setError(null);
    setOpen(false);

    try {
      await exportDocument(document.id, format, document.filename);
    } catch (err: unknown) {
      setError(toApiError(err).message);
    } finally {
      setDownloading(null);
    }
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <div className="relative" ref={menuRef}>
        <Button
          variant="secondary"
          className="w-auto px-3 py-1.5"
          disabled={!completed}
          loading={downloading !== null}
          title={
            completed
              ? 'Export the extracted content'
              : 'Export is available once processing finishes'
          }
          onClick={() => {
            if (downloading === null) {
              setOpen((previous: boolean) => !previous);
            }
          }}
        >
          <FileDown aria-hidden="true" className="size-4" />
          Export
          <span
            aria-hidden="true"
            className={cn(
              'ml-1 inline-block transition-transform',
              open && 'rotate-180'
            )}
          >
            ▾
          </span>
        </Button>

        {open && (
          <div
            role="menu"
            aria-label="Export document"
            className="absolute right-0 top-full z-50 mt-2 min-w-48 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg"
          >
            {OPTIONS.map(({ format, label }) => (
              <button
                key={format}
                type="button"
                role="menuitem"
                disabled={downloading !== null}
                onClick={() => void handleExport(format)}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      {error && (
        <p role="alert" className="text-xs text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
