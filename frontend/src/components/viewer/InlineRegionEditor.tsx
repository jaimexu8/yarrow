'use client';

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { correctionErrorDetail, updateRegionText } from '@/lib/corrections';
import { useViewer } from './ViewerContext';

/**
 * Inline editing of one region's extracted text. Escape cancels,
 * Ctrl/Cmd+Enter or the Save button submits, and failures surface inline
 * (a pending document, a read-only share, an over-long text and so on).
 */
export function InlineRegionEditor({
  documentId,
  regionId,
  initialText,
  pageNumber,
  onDone,
}: {
  documentId: string;
  regionId: string;
  initialText: string;
  pageNumber: number;
  onDone: () => void;
}) {
  const { applyRegionCorrection } = useViewer();
  const [value, setValue] = useState(initialText);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.focus();
    // Put the caret at the end, where a correction usually continues.
    const end = textarea.value.length;
    textarea.setSelectionRange(end, end);
  }, []);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await updateRegionText(documentId, regionId, value);
      // Update the rendered text in place; a later refresh reconciles with
      // the server copy. A no-change save keeps the region unedited.
      if (result.changed) applyRegionCorrection(regionId, value);
      onDone();
    } catch (caught) {
      setError(
        correctionErrorDetail(caught) ?? 'Could not save the correction.'
      );
      setBusy(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onDone();
      return;
    }
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      void save();
    }
  };

  return (
    <form
      data-region-edit={regionId}
      className="not-prose my-1"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <label htmlFor={`region-text-${regionId}`} className="sr-only">
        Corrected text of the region on page {pageNumber}
      </label>
      <textarea
        id={`region-text-${regionId}`}
        ref={textareaRef}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
        rows={Math.min(20, value.split('\n').length + 1)}
        disabled={busy}
        className="w-full resize-y rounded-md border border-slate-300 bg-white p-2 font-normal text-sm text-slate-900 shadow-inner focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-300 disabled:opacity-60"
      />
      <div className="mt-2 flex items-center justify-end gap-2">
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onDone}
            disabled={busy}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-sm text-red-600">
          {error}
        </p>
      )}
    </form>
  );
}
