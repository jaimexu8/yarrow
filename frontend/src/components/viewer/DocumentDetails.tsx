'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, Cpu, Gauge } from 'lucide-react';
import { cn } from '@/lib/cn';
import { getDocument, parseApiDate, type ModelUsage } from '@/lib/documents';
import { formatBytes } from '@/lib/storage';
import { isLowConfidence } from '@/lib/viewer';
import { useViewer } from './ViewerContext';

function modelLabel(name?: string | null, version?: string | null): string {
  return [name, version].filter(Boolean).join(' ') || 'Unknown';
}

export function ModelInfo({ className }: { className?: string }) {
  const { document } = useViewer();
  const label = modelLabel(document.model_name, document.model_version);

  return (
    <span
      title={`Model: ${label}`}
      className={cn(
        'inline-flex min-w-0 items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-700',
        className
      )}
    >
      <Cpu aria-hidden="true" className="size-3.5 shrink-0" />
      <span className="truncate">Model: {label}</span>
    </span>
  );
}

// The header chip summarizing how many pages were read with high confidence
export function DocumentDetails() {
  const { document, tree, showLowConfidence, setShowLowConfidence } =
    useViewer();
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<ModelUsage[]>([]);
  const panelId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  function onPointerDown(event: PointerEvent) {
    if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key !== 'Escape') return;
    setOpen(false);
    buttonRef.current?.focus();
  }

  useEffect(() => {
    if (!open) return;

    window.document.addEventListener('pointerdown', onPointerDown);
    window.document.addEventListener('keydown', onKeyDown);

    return () => {
      window.document.removeEventListener('pointerdown', onPointerDown);
      window.document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let current = true;

    getDocument(document.id)
      .then((detail) => {
        if (current) setModels(detail.models);
      })
      .catch(() => {
        if (current) setModels([]);
      });

    return () => {
      current = false;
    };
  }, [open, document.id, document.processed_at]);

  if (!tree || tree.stats.page_count === 0) return null;

  const { page_count: pages, high_confidence_page_count: confident } =
    tree.stats;
  const allConfident = confident >= pages;
  const share = Math.round((confident / pages) * 100);
  const lowRegions = tree.pages.reduce(
    (total, page) => total + page.regions.filter(isLowConfidence).length,
    0
  );

  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
        title="Document details"
        className={cn(
          'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset transition-colors',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900',
          allConfident
            ? 'bg-emerald-50 text-emerald-800 ring-emerald-200 hover:bg-emerald-100'
            : 'bg-amber-50 text-amber-900 ring-amber-200 hover:bg-amber-100'
        )}
      >
        <Gauge aria-hidden="true" className="size-3.5" />
        <span className="tabular-nums">
          {confident}/{pages}
          <span className="hidden sm:inline"> high confidence pages</span>
          <span className="sr-only sm:hidden"> high confidence pages</span>
        </span>
        <ChevronDown
          aria-hidden="true"
          className={cn('size-3.5 transition-transform', open && 'rotate-180')}
        />
      </button>

      {open && (
        <div
          id={panelId}
          role="region"
          aria-label="Document details"
          className="absolute right-0 top-full z-30 mt-2 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-slate-200 bg-white p-4 text-sm shadow-lg"
        >
          <h2 className="font-semibold text-slate-900">Document details</h2>

          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-slate-600">
            <dt>Pages</dt>
            <dd className="text-right tabular-nums text-slate-900">{pages}</dd>
            {document.created_at && (
              <>
                <dt>Uploaded</dt>
                <dd className="text-right text-slate-900">
                  {parseApiDate(document.created_at).toLocaleDateString(
                    undefined,
                    { dateStyle: 'medium' }
                  )}
                </dd>
              </>
            )}
            <dt>Size</dt>
            <dd className="text-right tabular-nums text-slate-900">
              {formatBytes(document.file_size_bytes)}
            </dd>
            <dt>Model</dt>
            <dd className="text-right text-slate-900 [overflow-wrap:anywhere]">
              {modelLabel(document.model_name, document.model_version)}
            </dd>
            <dt>Processed</dt>
            <dd className="text-right text-slate-900">
              {document.processed_at
                ? parseApiDate(document.processed_at).toLocaleString(
                    undefined,
                    { dateStyle: 'medium', timeStyle: 'short' }
                  )
                : 'Not recorded'}
            </dd>
          </dl>

          {models.length > 1 && (
            <div className="mt-3 rounded-lg bg-slate-50 p-2.5 text-xs text-slate-600">
              <p className="font-medium text-slate-900">
                Pages were processed by {models.length} model versions
              </p>
              <ul className="mt-1.5 space-y-1">
                {models.map((model) => (
                  <li
                    key={`${model.model_name}\u0000${model.model_version}`}
                    className="flex justify-between gap-3"
                  >
                    <span className="[overflow-wrap:anywhere]">
                      {modelLabel(model.model_name, model.model_version)}
                    </span>
                    <span className="shrink-0 tabular-nums">
                      {model.page_count}{' '}
                      {model.page_count === 1 ? 'page' : 'pages'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-4 border-t border-slate-100 pt-4">
            <h3 className="font-medium text-slate-900">Confidence</h3>
            <p className="mt-1 text-slate-600">
              <span className="font-semibold tabular-nums text-slate-900">
                {confident} of {pages}
              </span>{' '}
              {pages === 1 ? 'page was' : 'pages were'} read with high
              confidence.
            </p>
            <div
              role="img"
              aria-label={`${share}% of high confidence pages`}
              className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100"
            >
              <div
                className={cn(
                  'h-full rounded-full',
                  allConfident ? 'bg-emerald-500' : 'bg-amber-500'
                )}
                style={{ width: `${share}%` }}
              />
            </div>

            {lowRegions > 0 && (
              <div className="mt-3 flex items-center justify-between gap-3">
                <p className="text-slate-600">
                  <span className="tabular-nums">{lowRegions}</span>{' '}
                  {lowRegions === 1 ? 'region' : 'regions'} to review
                </p>
                <button
                  type="button"
                  aria-pressed={showLowConfidence}
                  onClick={() => setShowLowConfidence(!showLowConfidence)}
                  className="rounded-md px-2 py-1 text-xs font-medium text-slate-900 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
                >
                  {showLowConfidence ? 'Hide highlights' : 'Show highlights'}
                </button>
              </div>
            )}

            {tree.stats.warning_count > 0 && (
              <p className="mt-2 text-slate-600">
                <span className="tabular-nums">{tree.stats.warning_count}</span>{' '}
                quality{' '}
                {tree.stats.warning_count === 1 ? 'warning' : 'warnings'}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
