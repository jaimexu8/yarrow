import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import type { DocumentSummary } from '@/lib/documents';
import type { DocumentTree, TableCellNode } from '@/lib/viewer';

const search = vi.hoisted(() => ({ params: new URLSearchParams() }));

vi.mock('next/navigation', () => ({
  useSearchParams: () => search.params,
}));

vi.mock('@/lib/viewer', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/viewer')>();
  return { ...actual, getDocumentTree: vi.fn() };
});

vi.mock('@/lib/tableOperations', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/tableOperations')>();
  return {
    ...actual,
    getMergeCandidates: vi.fn().mockResolvedValue({
      candidates: [],
      splittable_table_ids: [],
      can_edit: false,
    }),
  };
});

vi.mock('@/components/viewer/SplitViewer', () => ({
  SplitViewer: ({ extracted }: { extracted: ReactNode }) => (
    <div>{extracted}</div>
  ),
}));
vi.mock('@/components/viewer/OriginalPane', () => ({
  OriginalPane: () => null,
}));
vi.mock('@/components/viewer/BoundingBoxCanvas', () => ({
  BoundingBoxCanvas: () => null,
}));
vi.mock('@/components/viewer/ReprocessBar', () => ({
  ReprocessBar: () => null,
  ReprocessButton: () => null,
}));
vi.mock('@/components/viewer/TableOperationsMenu', () => ({
  TableOperationsDropdown: () => null,
}));
vi.mock('@/components/viewer/ExportMenu', () => ({
  ExportMenu: () => null,
}));

import DocumentViewerPage from './page';
import { getDocumentTree } from '@/lib/viewer';

const scrollIntoView = vi.fn();

beforeAll(() => {
  Element.prototype.scrollIntoView = scrollIntoView;
  window.matchMedia = vi.fn().mockReturnValue({
    matches: true,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const DOCUMENT = {
  id: 'doc-1',
  filename: 'q3-report.pdf',
  status: 'completed',
  page_count: 1,
} as DocumentSummary;

function cell(
  id: string,
  row: number,
  col: number,
  text: string
): TableCellNode {
  return {
    id,
    row,
    col,
    row_span: 1,
    col_span: 1,
    text,
    is_header: row === 0,
    bbox: { x0: 0, y0: 0, x1: 1, y1: 1 },
    page_number: 1,
    region_table_id: 'rt-1',
    confidence: null,
  };
}

function tree(): DocumentTree {
  return {
    schema_version: 1,
    generated_at: '2026-11-04T00:00:00Z',
    document: DOCUMENT,
    pages_included: [1],
    can_edit: false,
    pages: [
      {
        page_number: 1,
        width: null,
        height: null,
        status: 'completed',
        has_thumbnail: false,
        error_message: null,
        image_key: null,
        regions: [
          {
            id: 'region-table',
            page_number: 1,
            reading_order: 0,
            region_type: 'table',
            bbox: { x0: 0, y0: 0, x1: 100, y1: 40 },
            confidence: 0.9,
            text: null,
            text_confidence: null,
            table_id: 'table-1',
            region_table_id: 'rt-1',
            is_first_table_part: true,
            image_key: null,
            caption: null,
          },
        ],
        warnings: [],
      },
    ],
    tables: [
      {
        id: 'table-1',
        title: null,
        row_count: 2,
        col_count: 2,
        is_stitched: false,
        has_spans: false,
        first_page: 1,
        last_page: 1,
        parts: [
          {
            region_table_id: 'rt-1',
            region_id: 'region-table',
            page_number: 1,
            reading_order: 0,
            row_start: 0,
            row_end: 1,
            col_start: 0,
            col_end: 1,
          },
        ],
        row_sources: [
          { row: 0, page_number: 1 },
          { row: 1, page_number: 1 },
        ],
        cells: [
          cell('cell-head-1', 0, 0, 'Metric'),
          cell('cell-head-2', 0, 1, 'Value'),
          cell('cell-revenue', 1, 0, 'Net revenue'),
          cell('cell-amount', 1, 1, '1,204'),
        ],
      },
    ],
    stats: {
      page_count: 1,
      high_confidence_page_count: 1,
      region_count: 1,
      table_count: 1,
      stitched_table_count: 0,
      warning_count: 0,
    },
  };
}

async function openViewer(query: string) {
  search.params = new URLSearchParams(query);
  vi.mocked(getDocumentTree).mockResolvedValue(tree());
  const params = Promise.resolve({ id: 'doc-1' });
  await act(async () => {
    render(<DocumentViewerPage params={params} />);
  });
  await screen.findByText('Metric');
}

function cellElement(id: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`[data-cell-id="${id}"]`);
  if (!element) throw new Error(`no cell ${id}`);
  return element;
}

const HIGHLIGHT = ['ring-2', 'ring-amber-400', 'bg-amber-50'];

it('highlights and scrolls to the cell named by ?cell=', async () => {
  await openViewer('page=1&region=region-table&cell=cell-revenue&q=revenue');

  const matched = cellElement('cell-revenue');
  expect(matched.tagName).toBe('TD');
  for (const name of HIGHLIGHT) expect(matched.classList).toContain(name);
  for (const other of ['cell-head-1', 'cell-head-2', 'cell-amount']) {
    expect(cellElement(other).classList).not.toContain('ring-2');
  }

  await waitFor(() => {
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center' });
  });
  expect(scrollIntoView.mock.contexts).toContain(matched);
});

it('highlights a matched header cell over its header background', async () => {
  await openViewer('region=region-table&cell=cell-head-2&q=value');

  const matched = cellElement('cell-head-2');
  expect(matched.tagName).toBe('TH');
  for (const name of HIGHLIGHT) expect(matched.classList).toContain(name);
  expect(matched.classList).not.toContain('bg-slate-50');
  expect(cellElement('cell-head-1').classList).toContain('bg-slate-50');
});

it('highlights no cell without ?cell=', async () => {
  await openViewer('region=region-table&q=revenue');

  for (const id of ['cell-head-1', 'cell-revenue', 'cell-amount']) {
    expect(cellElement(id).classList).not.toContain('ring-2');
  }
});
