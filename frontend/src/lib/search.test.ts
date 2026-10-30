import { afterEach, expect, it, vi } from 'vitest';
import api from './api';
import { searchDocuments, viewerHighlightTerm, isApproxMatch } from './search';

vi.mock('./api', () => ({ default: { get: vi.fn() } }));

afterEach(() => {
  vi.clearAllMocks();
});

it('sends the query term', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: [] });
  await searchDocuments('quarterly');
  expect(api.get).toHaveBeenCalledWith('/api/v1/search/', {
    params: { q: 'quarterly' },
  });
});

it('omits filters that are not set', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: [] });
  await searchDocuments('quarterly', { dateFrom: '2025-06-01' });
  expect(api.get).toHaveBeenCalledWith('/api/v1/search/', {
    params: { q: 'quarterly', date_from: '2025-06-01' },
  });
});

it('sends every filter that is set', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: [] });
  await searchDocuments('', {
    dateFrom: '2025-06-01',
    dateTo: '2025-06-30',
    fileType: 'application/pdf',
  });
  expect(api.get).toHaveBeenCalledWith('/api/v1/search/', {
    params: {
      q: '',
      date_from: '2025-06-01',
      date_to: '2025-06-30',
      file_type: 'application/pdf',
    },
  });
});

it('forwards the response body', async () => {
  const hits = [
    {
      id: 'd1',
      filename: 'notes.pdf',
      snippets: [
        {
          region_id: 'r1',
          page_number: 1,
          text: '...',
          match_start: 0,
          match_end: 9,
        },
      ],
    },
  ];
  vi.mocked(api.get).mockResolvedValue({ data: hits });
  await expect(searchDocuments('quarterly')).resolves.toEqual(hits);
});

// the term sent to the viewer for highlighting, which must match
// the first term of the parsed query (the snippet span is built from it). A
// leading quoted phrase is used verbatim (whitespace-normalised); otherwise
// the first word.
it('viewerHighlightTerm: leading quoted phrase is the first term', () => {
  expect(viewerHighlightTerm('"quarterly summary" extra')).toBe(
    'quarterly summary'
  );
});

it('viewerHighlightTerm: a phrase not at the start is not the first term', () => {
  expect(viewerHighlightTerm('extra "quarterly summary"')).toBe('extra');
});

it('viewerHighlightTerm: normalises whitespace in the phrase', () => {
  expect(viewerHighlightTerm('"quarterly   summary"')).toBe(
    'quarterly summary'
  );
});

it('viewerHighlightTerm: first word when no phrase', () => {
  expect(viewerHighlightTerm('quarterly summary report')).toBe('quarterly');
});

it('viewerHighlightTerm: single word', () => {
  expect(viewerHighlightTerm('quarterly')).toBe('quarterly');
});

it('viewerHighlightTerm: empty when nothing usable', () => {
  expect(viewerHighlightTerm('   ')).toBe('');
  expect(viewerHighlightTerm('""')).toBe('');
});

it('isApproxMatch: flags a confusable OCR match', () => {
  // query 2024, text "2O24" -> matched span differs from the term
  expect(
    isApproxMatch({ text: 'in 2O24 the', match_start: 3, match_end: 7 }, '2024')
  ).toBe(true);
});

it('isApproxMatch: does not flag an exact match', () => {
  expect(
    isApproxMatch({ text: 'in 2024 the', match_start: 3, match_end: 7 }, '2024')
  ).toBe(false);
});

it('isApproxMatch: case differences are not flagged', () => {
  expect(
    isApproxMatch(
      { text: 'the Quarterly summary', match_start: 4, match_end: 13 },
      'quarterly'
    )
  ).toBe(false);
});

it('isApproxMatch: a one-edit typo is flagged', () => {
  expect(
    isApproxMatch(
      { text: 'we recieve the', match_start: 3, match_end: 9 },
      'receive'
    )
  ).toBe(true);
});

it('isApproxMatch: extra spacing in the phrase is not flagged', () => {
  // the backend allows any whitespace run inside a phrase
  expect(
    isApproxMatch(
      { text: 'the quarterly  summary is', match_start: 4, match_end: 22 },
      'quarterly summary'
    )
  ).toBe(false);
});

it('isApproxMatch: empty term never flags', () => {
  expect(isApproxMatch({ text: 'abc', match_start: 0, match_end: 3 }, '')).toBe(
    false
  );
});
