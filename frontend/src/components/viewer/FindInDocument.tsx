'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, Search, X } from 'lucide-react';
import { findInDocument, type DocumentHit } from '@/lib/documentSearch';
import { cn } from '@/lib/cn';
import { useViewer } from './ViewerContext';

const ICON_BUTTON =
  'rounded-md p-1.5 text-slate-600 hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:bg-transparent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900';

function showExtracted(
  setMode: (mode: 'original' | 'split' | 'extracted') => void
) {
  setMode(
    window.matchMedia('(min-width: 768px)').matches ? 'split' : 'extracted'
  );
}

export function FindInDocument() {
  const {
    tree,
    mode,
    highlightRegionId,
    highlightQuery,
    setHighlight,
    setMode,
  } = useViewer();
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const regionRef = useRef(highlightRegionId);
  const draftRef = useRef(highlightQuery);
  const [draft, setDraft] = useState(highlightQuery);
  const [hits, setHits] = useState<DocumentHit[]>([]);
  const [active, setActive] = useState(0);

  regionRef.current = highlightRegionId;

  useEffect(() => {
    if (!tree) return;
    const term = draft.trim();
    const nextHits = findInDocument(tree, term);
    const draftChanged = draftRef.current !== draft;
    draftRef.current = draft;
    setHits(nextHits);

    if (!term) {
      setHighlight(null, '');
      setActive(0);
      return;
    }

    const keep = nextHits.findIndex(
      (hit) => hit.regionId === regionRef.current
    );
    const index = !draftChanged && keep >= 0 ? keep : 0;
    setActive(index);
    setHighlight(nextHits[index]?.regionId ?? null, term);
    if (nextHits.length > 0 && mode === 'original') {
      showExtracted(setMode);
    }
  }, [tree, draft, mode, setHighlight, setMode]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'f') {
        event.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  function go(delta: number) {
    if (hits.length === 0) return;
    const index = (active + delta + hits.length) % hits.length;
    setActive(index);
    setHighlight(hits[index].regionId, draft.trim());
    if (mode === 'original') showExtracted(setMode);
  }

  function clear() {
    setDraft('');
    inputRef.current?.focus();
  }

  const term = draft.trim();
  const label =
    !term || !tree
      ? ''
      : hits.length === 0
        ? 'No matches'
        : `${active + 1} of ${hits.length}`;

  return (
    <div className="flex min-w-0 items-center gap-1">
      <label htmlFor={inputId} className="sr-only">
        Find in this document
      </label>
      <div className="relative min-w-0">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-slate-400"
        />
        <input
          ref={inputRef}
          id={inputId}
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              go(event.shiftKey ? -1 : 1);
            }
            if (event.key === 'Escape') {
              event.preventDefault();
              clear();
            }
          }}
          placeholder="Find in document"
          autoComplete="off"
          className="w-40 rounded-md border-slate-300 py-1.5 pl-7 pr-7 text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-900 focus:ring-slate-900 sm:w-52"
        />
        {draft && (
          <button
            type="button"
            className={cn(
              ICON_BUTTON,
              'absolute right-0.5 top-1/2 -translate-y-1/2'
            )}
            onClick={clear}
          >
            <X aria-hidden="true" className="size-3.5" />
            <span className="sr-only">Clear find</span>
          </button>
        )}
      </div>
      <p
        aria-live="polite"
        className="hidden min-w-[4.5rem] text-xs tabular-nums text-slate-500 sm:block"
      >
        {label}
      </p>
      <button
        type="button"
        className={ICON_BUTTON}
        disabled={hits.length === 0}
        onClick={() => go(-1)}
      >
        <ChevronUp aria-hidden="true" className="size-4" />
        <span className="sr-only">Previous match</span>
      </button>
      <button
        type="button"
        className={ICON_BUTTON}
        disabled={hits.length === 0}
        onClick={() => go(1)}
      >
        <ChevronDown aria-hidden="true" className="size-4" />
        <span className="sr-only">Next match</span>
      </button>
    </div>
  );
}
