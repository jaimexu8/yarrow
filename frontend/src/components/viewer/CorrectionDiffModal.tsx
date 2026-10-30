'use client';

import { useState } from 'react';
import { Undo2, X } from 'lucide-react';
import {
  correctionErrorDetail,
  undoRegionCorrection as undoCorrectionApi,
} from '@/lib/corrections';
import { diffIsNeutral, wordDiffParts } from '@/lib/textDiff';
import { Modal } from '@/components/ui/Modal';
import { useViewer } from './ViewerContext';

/**
 * One corrected region against its raw OCR output, one line below the other
 * with word-level highlighting, plus the reversion.
 */
export function CorrectionDiffModal({
  documentId,
  regionId,
  pageNumber,
  editedText,
  originalText,
  canRevert = true,
  open,
  onClose,
}: {
  documentId: string;
  regionId: string;
  pageNumber: number;
  editedText: string;
  originalText: string;
  /** False for read-only viewers, who can compare but not undo here. */
  canRevert?: boolean;
  open: boolean;
  onClose: () => void;
}) {
  const { undoRegionCorrection, refresh } = useViewer();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parts = wordDiffParts(originalText, editedText);

  const revert = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await undoCorrectionApi(documentId, regionId);
      if (result.changed) undoRegionCorrection(regionId);
      // Reconcile with the server (updated_at, anything shared) and close.
      refresh();
      onClose();
    } catch (caught) {
      setError(
        correctionErrorDetail(caught) ?? 'Could not revert the correction.'
      );
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissible={!busy}
      title={`Correction on page ${pageNumber}`}
      className="max-w-xl"
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            <X aria-hidden="true" className="mr-1 inline-block size-4" />
            Close
          </button>
          {canRevert && (
            <button
              type="button"
              onClick={() => void revert()}
              disabled={busy}
              className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-60"
            >
              <Undo2 aria-hidden="true" className="mr-1 inline-block size-4" />
              {busy ? 'Reverting…' : 'Undo correction'}
            </button>
          )}
        </>
      }
    >
      {error && (
        <p role="alert" className="mb-2 text-sm text-red-600">
          {error}
        </p>
      )}
      <dl className="space-y-3">
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Original extraction
          </dt>
          <dd
            // Struck-through words are the additions' counterparts
            className="mt-1 whitespace-pre-wrap rounded-md bg-rose-50 p-2 text-sm text-slate-700"
          >
            {parts.map((part, index) =>
              part.removed ? (
                <del key={index} className="rounded bg-rose-200 text-rose-900">
                  {part.value}
                </del>
              ) : (
                !part.added && <span key={index}>{part.value}</span>
              )
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Corrected text
          </dt>
          <dd
            // Standalone additions, e.g. spelled-out words
            className="mt-1 whitespace-pre-wrap rounded-md bg-emerald-50 p-2 text-sm text-slate-700"
          >
            {parts.map((part, index) =>
              part.added ? (
                <ins
                  key={index}
                  className="rounded bg-emerald-200 text-emerald-900 no-underline"
                >
                  {part.value}
                </ins>
              ) : (
                !part.removed && <span key={index}>{part.value}</span>
              )
            )}
          </dd>
        </div>
      </dl>
      {diffIsNeutral(parts) && (
        <p className="mt-2 text-sm text-slate-500">
          The corrected text matches the original extraction.
        </p>
      )}
    </Modal>
  );
}
