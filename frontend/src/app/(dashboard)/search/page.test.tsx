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
          ...snippet,
        },
      ],
    },
  ];
}

it('flags snippets that come from corrected regions', async () => {
  vi.mocked(searchDocuments).mockResolvedValue(hit({ edited: true }));
  render(<SearchPage />);
  fireEvent.change(screen.getByLabelText(/Search/), {
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
  fireEvent.change(screen.getByLabelText(/Search/), {
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
      snippets: [{ region_id: 'r1', page_number: 1, ...snippet }],
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
  fireEvent.change(screen.getByLabelText(/Search/), {
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

it('flags an approximate OCR match with an "approx" badge', async () => {
  // search 2024, but the matched text is the OCR'd "2O24"
  vi.mocked(searchDocuments).mockResolvedValue(
    hitWith({ text: 'in 2O24 the numbers', match_start: 3, match_end: 7 })
  );
  render(<SearchPage />);
  fireEvent.change(screen.getByLabelText(/Search/), {
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
  fireEvent.change(screen.getByLabelText(/Search/), {
    target: { value: 'quarterly' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  await waitFor(() => {
    expect(screen.getByText('quarterly')).toBeTruthy();
  });
  expect(screen.queryByText('approx')).toBeNull();
});
