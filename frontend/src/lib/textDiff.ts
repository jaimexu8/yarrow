import { diffWords } from 'diff';

export type DiffPart = {
  value: string;
  added: boolean;
  removed: boolean;
};

/**
 * Word-level comparison of a corrected region against the raw OCR output,
 * for the diff view next to each corrected region (us-100).
 */
export function wordDiffParts(before: string, after: string): DiffPart[] {
  return diffWords(before, after).map((part) => ({
    value: part.value,
    added: part.added ?? false,
    removed: part.removed ?? false,
  }));
}

/** True when the diff shows no change at all beyond formatting. */
export function diffIsNeutral(parts: DiffPart[]): boolean {
  return !parts.some((part) => part.added || part.removed);
}
