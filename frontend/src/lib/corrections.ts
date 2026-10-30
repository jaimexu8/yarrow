import api from './api';

/** Response of a region text correction or a reversion. */
export type CorrectionResult = {
  changed: boolean;
  document_updated_at?: string | null;
};

/**
 * Replace one region's extracted text. The server keeps the raw OCR output
 * alongside the correction, so it can be reverted later.
 */
export async function updateRegionText(
  documentId: string,
  regionId: string,
  text: string
): Promise<CorrectionResult> {
  const res = await api.put<CorrectionResult>(
    `/api/v1/documents/${documentId}/regions/${regionId}/text`,
    { text }
  );
  return res.data;
}

/** Revert a corrected region back to the raw OCR output. */
export async function undoRegionCorrection(
  documentId: string,
  regionId: string
): Promise<CorrectionResult> {
  const res = await api.delete<CorrectionResult>(
    `/api/v1/documents/${documentId}/regions/${regionId}/correction`
  );
  return res.data;
}

/**
 * The readable message from a backend error, or null when the error is not
 * one the correction flow caused. The endpoints nest the message in
 * ``detail.detail`` with a stable ``code`` next to it.
 */
export function correctionErrorDetail(error: unknown): string | null {
  const detail = isApiDetail(error);
  if (typeof detail === 'string') return detail;
  return detail?.detail ?? null;
}

/** The machine-readable error code, e.g. "DOCUMENT_READ_ONLY". */
export function correctionErrorCode(error: unknown): string | null {
  const detail = isApiDetail(error);
  return typeof detail === 'object' && detail !== null
    ? (detail.code ?? null)
    : null;
}

function isApiDetail(
  error: unknown
): string | { detail?: string; code?: string } | null {
  const response = (error as { response?: { data?: { detail?: unknown } } })
    ?.response;
  const detail = response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const inner = detail as { detail?: unknown; code?: unknown };
    return {
      detail: typeof inner.detail === 'string' ? inner.detail : undefined,
      code: typeof inner.code === 'string' ? inner.code : undefined,
    };
  }
  return null;
}
