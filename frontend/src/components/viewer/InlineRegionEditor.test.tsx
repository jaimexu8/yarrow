import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ViewerProvider, useViewer } from './ViewerContext';
import { InlineRegionEditor } from './InlineRegionEditor';
import type { DocumentSummary } from '@/lib/documents';
import type { DocumentTree, RegionNode } from '@/lib/viewer';
import type { ReactNode } from 'react';

vi.mock('@/lib/corrections', () => ({
  updateRegionText: vi.fn(),
  undoRegionCorrection: vi.fn(),
  correctionErrorDetail: (error: unknown) =>
    (error as { detail?: string } | undefined)?.detail ?? null,
  correctionErrorCode: () => null,
}));

import { updateRegionText } from '@/lib/corrections';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const REGION: RegionNode = {
  id: 'r1',
  page_number: 1,
  reading_order: 0,
  region_type: 'paragraph',
  bbox: { x0: 0, y0: 0, x1: 100, y1: 40 },
  confidence: 0.9,
  text: 'the quatrly summary',
  text_confidence: 0.8,
  is_edited: false,
  original_text: null,
  table_id: null,
  region_table_id: null,
  is_first_table_part: false,
  image_key: null,
  caption: null,
};

const DOCUMENT = {
  id: 'doc-1',
  filename: 'notes.pdf',
  status: 'completed',
  page_count: 1,
} as DocumentSummary;

function tree(region: RegionNode = REGION): DocumentTree {
  return {
    schema_version: 1,
    generated_at: '2026-10-26T00:00:00Z',
    document: DOCUMENT,
    pages_included: [1],
    can_edit: true,
    pages: [
      {
        page_number: 1,
        width: null,
        height: null,
        status: 'completed',
        error_message: null,
        image_key: null,
        regions: [region],
        warnings: [],
      },
    ],
    tables: [],
    stats: {
      page_count: 1,
      high_confidence_page_count: 1,
      region_count: 1,
      table_count: 0,
      stitched_table_count: 0,
      warning_count: 0,
    },
  };
}

// Captures the context tree, so a test can see the applied correction.
function TreeProbe() {
  const { tree: current } = useViewer();
  if (!current) return null;
  const region = current.pages[0].regions[0];
  return (
    <p data-testid="probe">
      {region.text}|{String(region.is_edited)}|{String(region.original_text)}
    </p>
  );
}

function TestProvider({
  children,
  probe,
}: {
  children: ReactNode;
  probe: boolean;
}) {
  return (
    <ViewerProvider document={DOCUMENT} tree={tree()} refresh={() => {}}>
      {children}
      {probe && <TreeProbe />}
    </ViewerProvider>
  );
}

it('saves through the API and applies the correction to the tree', async () => {
  vi.mocked(updateRegionText).mockResolvedValue({ changed: true });

  render(
    <TestProvider probe>
      <InlineRegionEditor
        documentId="doc-1"
        regionId="r1"
        initialText="the quatrly summary"
        pageNumber={1}
        onDone={() => {}}
      />
    </TestProvider>
  );

  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'the quarterly summary' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  await vi.waitFor(() => {
    expect(screen.getByTestId('probe').textContent).toBe(
      'the quarterly summary|true|the quatrly summary'
    );
  });
  expect(updateRegionText).toHaveBeenCalledWith(
    'doc-1',
    'r1',
    'the quarterly summary'
  );
});

it('cancels without saving', async () => {
  render(
    <TestProvider probe={false}>
      <InlineRegionEditor
        documentId="doc-1"
        regionId="r1"
        initialText="as extracted"
        pageNumber={1}
        onDone={() => {}}
      />
    </TestProvider>
  );

  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'changed but not saved' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(updateRegionText).not.toHaveBeenCalled();
});

it('escapes the editor with the Escape key', async () => {
  const onDone = vi.fn();
  render(
    <TestProvider probe={false}>
      <InlineRegionEditor
        documentId="doc-1"
        regionId="r1"
        initialText="as extracted"
        pageNumber={1}
        onDone={onDone}
      />
    </TestProvider>
  );

  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' });
  expect(onDone).toHaveBeenCalled();
  expect(updateRegionText).not.toHaveBeenCalled();
});

it('shows the server refusal inline', async () => {
  vi.mocked(updateRegionText).mockRejectedValue({
    detail: 'Document has not finished processing',
  });

  render(
    <TestProvider probe>
      <InlineRegionEditor
        documentId="doc-1"
        regionId="r1"
        initialText="too early"
        pageNumber={1}
        onDone={() => {}}
      />
    </TestProvider>
  );

  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  await vi.waitFor(() => {
    expect(
      screen.getByText('Document has not finished processing')
    ).toBeTruthy();
  });
  // The tree was not changed
  expect(screen.getByTestId('probe').textContent).toBe(
    'the quatrly summary|false|null'
  );
});
