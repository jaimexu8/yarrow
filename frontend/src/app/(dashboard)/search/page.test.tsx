import { afterEach, expect, it, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import SearchPage from './page';
import type { SearchHit } from '@/lib/search';

vi.mock('@/lib/search', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/search')>();
  // Keep the real pure helpers; mock only the network call.
  return { ...actual, searchDocuments: vi.fn() };
});

import { searchDocuments } from '@/lib/search';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function hit(snippet: { edited?: boolean }): SearchHit[] {
  return [
    {
      id: 'doc-1',
      filename: 'notes.pdf',
      snippets: [
        {
          region_id: 'r1',
          page_number: 1,
          text: 'the quarterly summary',
          match_start: 4,
          match_end: 13,
          source: 'text',
          table_cell: null,
          ...snippet,
        },
      ],
    },
  ];
}

it('flags snippets that come from corrected regions', async () => {
  vi.mocked(searchDocuments).mockResolvedValue(hit({ edited: true }));
  render(<SearchPage />);
  fireEvent.change(screen.getByLabelText('Search'), {
    target: { value: 'quarterly' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  await waitFor(() => {
    expect(screen.getByText('corrected')).toBeTruthy();
  });
});

it('does not flag untouched snippets', async () => {
  vi.mocked(searchDocuments).mockResolvedValue(hit({}));
  render(<SearchPage />);
  fireEvent.change(screen.getByLabelText('Search'), {
    target: { value: 'quarterly' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  await waitFor(() => {
    expect(screen.getByText('quarterly')).toBeTruthy();
  });
  expect(screen.queryByText('corrected')).toBeNull();
});

function hitWith(snippet: {
  text: string;
  match_start: number;
  match_end: number;
}): SearchHit[] {
  return [
    {
      id: 'doc-1',
      filename: 'notes.pdf',
      snippets: [
        {
          region_id: 'r1',
          page_number: 1,
          source: 'text',
          table_cell: null,
          ...snippet,
        },
      ],
    },
  ];
}

it('shows the exact-phrase hint', () => {
  render(<SearchPage />);
  expect(screen.getByText(/Wrap a phrase in double quotes/)).toBeTruthy();
});

it('sends the raw query (with quotes) to the API', async () => {
  vi.mocked(searchDocuments).mockResolvedValue([]);
  render(<SearchPage />);
  fireEvent.change(screen.getByLabelText('Search'), {
    target: { value: '"quarterly summary"' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  await waitFor(() => {
    expect(searchDocuments).toHaveBeenCalledWith(
      '"quarterly summary"',
      expect.anything()
    );
  });
});

it('sends the chosen sort and keeps it on the next search', async () => {
  vi.mocked(searchDocuments).mockResolvedValue([]);
  render(<SearchPage />);
  fireEvent.change(screen.getByLabelText('Search'), {
    target: { value: 'quarterly' },
  });
  fireEvent.change(screen.getByLabelText(/Sort/), {
    target: { value: 'name' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  await waitFor(() => {
    expect(searchDocuments).toHaveBeenCalledWith(
      'quarterly',
      expect.objectContaining({ sort: 'name' })
    );
  });

  vi.mocked(searchDocuments).mockClear();
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  await waitFor(() => {
    expect(searchDocuments).toHaveBeenCalledWith(
      'quarterly',
      expect.objectContaining({ sort: 'name' })
    );
  });
});

it('flags an approximate OCR match with an "approx" badge', async () => {
  // search 2024, but the matched text is the OCR'd "2O24"
  vi.mocked(searchDocuments).mockResolvedValue(
    hitWith({ text: 'in 2O24 the numbers', match_start: 3, match_end: 7 })
  );
  render(<SearchPage />);
  fireEvent.change(screen.getByLabelText('Search'), {
    target: { value: '2024' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  await waitFor(() => {
    expect(screen.getByText('approx')).toBeTruthy();
  });
});

it('does not flag an exact match', async () => {
  vi.mocked(searchDocuments).mockResolvedValue(
    hitWith({ text: 'the quarterly summary', match_start: 4, match_end: 13 })
  );
  render(<SearchPage />);
  fireEvent.change(screen.getByLabelText('Search'), {
    target: { value: 'quarterly' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  await waitFor(() => {
    expect(screen.getByText('quarterly')).toBeTruthy();
  });
  expect(screen.queryByText('approx')).toBeNull();
});

function cellHit(columnHeader: string | null): SearchHit[] {
  return [
    {
      id: 'doc-1',
      filename: 'q3-report.pdf',
      snippets: [
        {
          region_id: 'region-table',
          page_number: 4,
          text: 'Net revenue 1,204',
          match_start: 4,
          match_end: 11,
          edited: false,
          source: 'table_cell',
          table_cell: {
            table_id: 'table-1',
            region_table_id: 'rt-1',
            cell_id: 'cell-7',
            row: 7,
            col: 0,
            row_span: 1,
            col_span: 1,
            is_header: false,
            column_header: columnHeader,
          },
        },
      ],
    },
  ];
}

async function searchFor(term: string) {
  render(<SearchPage />);
  fireEvent.change(screen.getByLabelText('Search'), {
    target: { value: term },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
}

it('labels a table cell match with a badge, row, column and header', async () => {
  vi.mocked(searchDocuments).mockResolvedValue(cellHit('Metric'));
  await searchFor('revenue');

  expect(await screen.findByText('Table')).toBeTruthy();
  expect(screen.getByText('Row 8, Column 1 · Metric')).toBeTruthy();
});

it('omits the column header when the cell has none', async () => {
  vi.mocked(searchDocuments).mockResolvedValue(cellHit(null));
  await searchFor('revenue');

  expect(await screen.findByText('Row 8, Column 1')).toBeTruthy();
});

it('links a table cell match to the cell in the viewer', async () => {
  vi.mocked(searchDocuments).mockResolvedValue(cellHit('Metric'));
  await searchFor('revenue');

  const link = (await screen.findByText('Table')).closest('a');
  const url = new URL(link?.getAttribute('href') ?? '', 'http://localhost');
  expect(url.pathname).toBe('/documents/doc-1');
  expect(Object.fromEntries(url.searchParams)).toEqual({
    page: '4',
    region: 'region-table',
    cell: 'cell-7',
    q: 'revenue',
  });
});

it('does not badge or link text matches as table cells', async () => {
  vi.mocked(searchDocuments).mockResolvedValue(hit({}));
  await searchFor('quarterly');

  const link = (await screen.findByText('quarterly')).closest('a');
  expect(link?.getAttribute('href')).not.toContain('cell=');
  expect(screen.queryByText('Table')).toBeNull();
});

it('sends the chosen scope', async () => {
  vi.mocked(searchDocuments).mockResolvedValue([]);
  render(<SearchPage />);
  fireEvent.change(screen.getByLabelText('Search'), {
    target: { value: 'revenue' },
  });
  fireEvent.change(screen.getByLabelText('Search in'), {
    target: { value: 'tables' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  await waitFor(() => {
    expect(searchDocuments).toHaveBeenCalledWith(
      'revenue',
      expect.objectContaining({ scope: 'tables' })
    );
  });
});
