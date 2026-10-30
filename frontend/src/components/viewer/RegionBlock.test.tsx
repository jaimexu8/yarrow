import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ViewerProvider, useViewer } from './ViewerContext';
import { RegionBlock } from './regionRenderers';
import type { DocumentSummary } from '@/lib/documents';
import type { DocumentTree, RegionNode } from '@/lib/viewer';

vi.mock('@/lib/corrections', () => ({
  updateRegionText: vi.fn(),
  undoRegionCorrection: vi.fn(),
  correctionErrorDetail: (error: unknown) =>
    (error as { detail?: string } | undefined)?.detail ?? null,
  correctionErrorCode: () => null,
}));

import { undoRegionCorrection, updateRegionText } from '@/lib/corrections';

beforeAll(() => {
  // The diff modal is a native <dialog>, which jsdom does not implement.
  HTMLDialogElement.prototype.showModal = vi.fn(function (
    this: HTMLDialogElement
  ) {
    this.open = true;
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.open = false;
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function region(patch: Partial<RegionNode> = {}): RegionNode {
  return {
    id: 'r1',
    page_number: 1,
    reading_order: 0,
    region_type: 'paragraph',
    bbox: { x0: 0, y0: 0, x1: 100, y1: 40 },
    confidence: 0.9,
    text: 'the quatrly summary',
    text_confidence: 0.8,
    table_id: null,
    region_table_id: null,
    is_first_table_part: false,
    image_key: null,
    caption: null,
    ...patch,
  };
}

const DOCUMENT = {
  id: 'doc-1',
  filename: 'notes.pdf',
  status: 'completed',
  page_count: 1,
} as DocumentSummary;

function tree(region: RegionNode, canEdit = true): DocumentTree {
  return {
    schema_version: 1,
    generated_at: '2026-10-26T00:00:00Z',
    document: DOCUMENT,
    pages_included: [1],
    can_edit: canEdit,
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

// Draws the region from the context tree, so corrections applied to the
// tree are visible in the rendered block.
function BlockFromTree({
  context,
}: {
  context: { table: null; captioned: boolean };
}) {
  const { tree } = useViewer();
  const region = tree?.pages[0].regions[0];
  if (!region) return null;
  return <RegionBlock region={region} context={context} />;
}

function renderBlock(region: RegionNode, canEdit = true, refresh = () => {}) {
  return render(
    <ViewerProvider
      document={DOCUMENT}
      tree={tree(region, canEdit)}
      refresh={refresh}
    >
      <BlockFromTree context={{ table: null, captioned: false }} />
    </ViewerProvider>
  );
}

it('hides the affordances when the viewer cannot edit', () => {
  renderBlock(region(), false);
  expect(screen.queryByTitle('Correct this text')).toBeNull();
});

it('edits inline, saves, and then shows the edited badge', async () => {
  vi.mocked(updateRegionText).mockResolvedValue({ changed: true });
  renderBlock(region());

  fireEvent.click(screen.getByTitle('Correct this text'));
  const textarea = screen.getByRole('textbox');
  fireEvent.change(textarea, { target: { value: 'the quarterly summary' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  await vi.waitFor(() => {
    expect(
      screen.getByTitle(
        'This text was corrected; click to compare with the OCR output'
      )
    ).toBeTruthy();
  });
  // The paragraph now renders the corrected text
  expect(screen.getByText(/the quarterly summary/)).toBeTruthy();
  expect(updateRegionText).toHaveBeenCalledWith(
    'doc-1',
    'r1',
    'the quarterly summary'
  );
});

it('shows the diff and reverts a correction', async () => {
  vi.mocked(undoRegionCorrection).mockResolvedValue({ changed: true });
  renderBlock(
    region({
      text: 'the quarterly summary',
      is_edited: true,
      original_text: 'the quatrly summary',
    })
  );

  fireEvent.click(
    screen.getAllByTitle(
      'This text was corrected; click to compare with the OCR output'
    )[0]
  );

  // The diff shows the struck-through original and the added correction
  const dialog = screen.getByRole('dialog');
  expect(dialog.textContent).toContain('Original extraction');
  expect(dialog.textContent).toContain('quarterly');
  expect(screen.getByText('quatrly').tagName).toBe('DEL');
  expect(screen.getByText('quarterly').tagName).toBe('INS');

  fireEvent.click(screen.getByRole('button', { name: /Undo correction/ }));

  await vi.waitFor(() => {
    expect(undoRegionCorrection).toHaveBeenCalledWith('doc-1', 'r1');
  });
});

it('reverts a correction straight from the region header', async () => {
  vi.mocked(undoRegionCorrection).mockResolvedValue({ changed: true });
  renderBlock(
    region({
      text: 'the quarterly summary',
      is_edited: true,
      original_text: 'the quatrly summary',
    })
  );

  fireEvent.click(
    screen.getByTitle('Revert this correction to the raw OCR output')
  );

  await vi.waitFor(() => {
    expect(undoRegionCorrection).toHaveBeenCalledWith('doc-1', 'r1');
  });
  // The badge and the undo button disappear once the tree is reverted
  await vi.waitFor(() => {
    expect(
      screen.queryByTitle(
        'This text was corrected; click to compare with the OCR output'
      )
    ).toBeNull();
    expect(
      screen.queryByTitle('Revert this correction to the raw OCR output')
    ).toBeNull();
  });
});

it('cannot edit a table region', () => {
  renderBlock(region({ region_type: 'table', text: 'joined text' }));
  expect(screen.queryByTitle('Correct this text')).toBeNull();
});
