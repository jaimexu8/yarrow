import { afterEach, expect, it, vi } from 'vitest';
import api from './api';
import {
  listWarnings,
  warningDetail,
  warningHref,
  warningKind,
  warningLabel,
} from './warnings';

vi.mock('./api', () => ({ default: { get: vi.fn() } }));

afterEach(() => {
  vi.clearAllMocks();
});

it('fetches the warnings list', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: [] });
  await listWarnings();
  expect(api.get).toHaveBeenCalledWith('/api/v1/warnings/');
});

it('links to the page of the document', () => {
  expect(warningHref('doc-1', 5)).toBe('/documents/doc-1?page=5');
});

it.each([
  ['Image is blurry', 'blur'],
  ['Image is skewed by -6.2°', 'skew'],
  ['Page was auto-rotated by 45°', 'rotated'],
  ['Image is truncated', 'truncation'],
  ['Image is too dark', 'dark'],
  ['Image is too bright', 'bright'],
  ['Failed to decode image', 'unreadable'],
  ['Something new', 'other'],
  [null, 'other'],
])('reads %s as %s', (message, kind) => {
  expect(warningKind(message)).toBe(kind);
});

it('labels a tilt with its size, without the direction', () => {
  expect(warningLabel('Image is skewed by -6.2°')).toBe('Tilted 6.2°');
  expect(warningLabel('Image is skewed by 12.0°')).toBe('Tilted 12.0°');
});

it('labels a rotation with its size', () => {
  expect(warningLabel('Page was auto-rotated by 45°')).toBe('Rotated 45°');
  expect(warningLabel('Page was auto-rotated by -11°')).toBe('Rotated 11°');
});

it('labels the other kinds by name', () => {
  expect(warningLabel('Image is blurry')).toBe('Blurry');
  expect(warningLabel('Image is truncated')).toBe('Cut off');
});

it('falls back to the message for an unknown warning', () => {
  expect(warningLabel('Something new')).toBe('Warning');
  expect(warningDetail('Something new')).toBe('Something new');
  expect(warningDetail(null)).toBe('');
});
