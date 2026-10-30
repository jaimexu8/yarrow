import api from './api';

export type WarningEntry = {
  document_id: string;
  filename: string;
  page_number: number;
  warning_type: string | null;
  message: string | null;
};

export type WarningKind =
  | 'blur'
  | 'skew'
  | 'truncation'
  | 'dark'
  | 'bright'
  | 'rotated'
  | 'unreadable'
  | 'other';

export const FILTERABLE_KINDS: WarningKind[] = [
  'blur',
  'skew',
  'truncation',
  'dark',
  'bright',
  'rotated',
];

// Mapping from warning kind to human readable label and detail
export const WARNING_KINDS: Record<
  WarningKind,
  { label: string; detail: string }
> = {
  blur: { label: 'Blurry', detail: 'Text on this page may be misread.' },
  skew: {
    label: 'Tilted',
    detail: 'Lines and tables may be split or merged wrongly.',
  },
  rotated: {
    label: 'Rotated',
    detail: 'The page was turned upright automatically before OCR.',
  },

  truncation: {
    label: 'Cut off',
    detail: 'Text reaches the edge, so some content may be missing.',
  },

  dark: { label: 'Too dark', detail: 'Faint text may be missed.' },
  bright: { label: 'Too bright', detail: 'Faint text may be missed.' },

  unreadable: {
    label: 'Unreadable',
    detail: "The page image couldn't be checked.",
  },
  other: { label: 'Warning', detail: '' },
};

// Mapping from message prefixes to warning kinds. Keep in step with quality_checker.py.
const MESSAGE_PREFIXES: [string, WarningKind][] = [
  ['Image is blurry', 'blur'],
  ['Image is skewed', 'skew'],
  ['Page was auto-rotated', 'rotated'],
  ['Image is truncated', 'truncation'],
  ['Image is too dark', 'dark'],
  ['Image is too bright', 'bright'],
  ['Failed to decode image', 'unreadable'],
];

export function warningKind(message: string | null): WarningKind {
  const match = MESSAGE_PREFIXES.find(([prefix]) =>
    message?.startsWith(prefix)
  );
  return match ? match[1] : 'other';
}

// Translate a warning message into a human-readable label
export function warningLabel(message: string | null): string {
  const kind = warningKind(message);
  if (kind === 'skew') {
    const angle = message?.match(/(-?\d+(?:\.\d+)?)°/);
    if (angle) return `Tilted ${Math.abs(Number(angle[1])).toFixed(1)}°`;
  }
  if (kind === 'rotated') {
    const angle = message?.match(/(-?\d+(?:\.\d+)?)°/);
    if (angle) return `Rotated ${Math.abs(Number(angle[1]))}°`;
  }
  return WARNING_KINDS[kind].label;
}

// Translate a warning message into a human-readable detail
export function warningDetail(message: string | null): string {
  const kind = warningKind(message);
  return kind === 'other' ? (message ?? '') : WARNING_KINDS[kind].detail;
}

// Get the URL of the document page the warning is about
export function warningHref(documentId: string, pageNumber: number): string {
  return `/documents/${documentId}?page=${pageNumber}`;
}

// List all quality warnings across the user's own documents, newest document first
export async function listWarnings(): Promise<WarningEntry[]> {
  const res = await api.get<WarningEntry[]>('/api/v1/warnings/');
  return res.data;
}
