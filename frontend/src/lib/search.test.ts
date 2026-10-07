import { afterEach, expect, it, vi } from 'vitest';
import api from './api';
import { searchDocuments } from './search';

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
