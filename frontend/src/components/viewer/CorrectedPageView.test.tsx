import { afterEach, expect, test, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import type { DocumentSummary } from '@/lib/documents';
import { getPageImage } from '@/lib/viewer';
import { CorrectedPageView } from './PageView';

const { viewer } = vi.hoisted(() => ({
  viewer: {
    document: null as unknown as DocumentSummary,
    tree: null as unknown,
    canEdit: true,
    registerPage: vi.fn(),
    pageRotations: {} as Record<number, number>,
    rotatePage: vi.fn(),
    savePageRotation: vi.fn(async () => null),
    resetPageRotation: vi.fn(async () => null),
  },
}));

vi.mock('@/lib/viewer', () => ({
  getPageImage: vi.fn(),
}));

vi.mock('./ViewerContext', () => ({
  useViewer: () => viewer,
}));

// jsdom has no layout, so PageFrame's ResizeObserver never fires; a no-op
// observer keeps the frame rendering.
class FakeResizeObserver {
  observe() {}
  disconnect() {}
}

// A fake image that reports a natural size and loads synchronously on src set.
// jsdom's real Image never fires onload, so CorrectedPageView would stay in
// its loading state forever.
class FakeImage {
  naturalWidth = 800;
  naturalHeight = 1000;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private _src = '';
  get src() {
    return this._src;
  }
  set src(value: string) {
    this._src = value;
    if (this.onload) this.onload();
  }
}

function documentSummary(): DocumentSummary {
  return {
    id: 'doc-1',
    filename: 'report.pdf',
    file_size_bytes: 1000,
    file_type: 'application/pdf',
    page_count: 1,
    status: 'completed',
    error_message: null,
    created_at: null,
    folder_id: null,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  viewer.tree = null;
  viewer.pageRotations = {};
});

test('shows the corrected upright image from the page-image endpoint', async () => {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => 'blob:corrected-url'),
    revokeObjectURL: vi.fn(),
  });
  viewer.document = documentSummary();
  vi.mocked(getPageImage).mockResolvedValueOnce(
    new Blob(['png'], { type: 'image/png' })
  );

  render(
    <CorrectedPageView
      documentId="doc-1"
      pageNumber={3}
      fallbackAspectRatio={0.8}
    />
  );

  // The corrected page is fetched through the page-image endpoint for this page.
  await waitFor(() => {
    expect(getPageImage).toHaveBeenCalledWith('doc-1', 3, undefined);
  });

  // The upright image is rendered, not the raw original.
  const img = await screen.findByAltText('Corrected page 3 of report.pdf');
  expect(img.getAttribute('src')).toBe('blob:corrected-url');
});

test('shows a message when the corrected image cannot be loaded', async () => {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => 'blob:corrected-url'),
    revokeObjectURL: vi.fn(),
  });
  viewer.document = documentSummary();
  vi.mocked(getPageImage).mockRejectedValueOnce(new Error('nope'));

  render(
    <CorrectedPageView
      documentId="doc-1"
      pageNumber={3}
      fallbackAspectRatio={0.8}
    />
  );

  await screen.findByText(/corrected page couldn/i);
});

test('the rotate button turns the page clockwise by 90 degrees at a time', async () => {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => 'blob:corrected-url'),
    revokeObjectURL: vi.fn(),
  });
  viewer.document = documentSummary();
  vi.mocked(getPageImage).mockResolvedValueOnce(
    new Blob(['png'], { type: 'image/png' })
  );

  render(
    <CorrectedPageView
      documentId="doc-1"
      pageNumber={3}
      fallbackAspectRatio={0.8}
    />
  );

  const button = screen.getByRole('button', {
    name: /rotate page 3 clockwise/i,
  });
  // A fresh page shows just the icon, no degree label yet.
  expect(button.textContent).not.toMatch(/\d+°/);

  // Each click asks the context to turn this page by another quarter turn.
  fireEvent.click(button);
  fireEvent.click(button);
  fireEvent.click(button);
  expect(vi.mocked(viewer.rotatePage)).toHaveBeenCalledTimes(3);
  expect(vi.mocked(viewer.rotatePage)).toHaveBeenNthCalledWith(1, 3);
  expect(vi.mocked(viewer.rotatePage)).toHaveBeenNthCalledWith(2, 3);
  expect(vi.mocked(viewer.rotatePage)).toHaveBeenNthCalledWith(3, 3);
});

function treeWithPage(overrides: Record<string, unknown> = {}): unknown {
  return {
    pages: [
      {
        page_number: 3,
        image_key: null,
        manual_rotation_deg: null,
        warnings: [],
        ...overrides,
      },
    ],
  };
}

test('the save rotation button persists the on-screen rotation', async () => {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => 'blob:corrected-url'),
    revokeObjectURL: vi.fn(),
  });
  viewer.document = documentSummary();
  viewer.tree = treeWithPage();
  viewer.pageRotations = { 3: 90 };
  vi.mocked(getPageImage).mockResolvedValueOnce(
    new Blob(['png'], { type: 'image/png' })
  );

  render(
    <CorrectedPageView
      documentId="doc-1"
      pageNumber={3}
      fallbackAspectRatio={0.8}
    />
  );

  // A turned page offers a save button; clicking it persists the rotation.
  const save = await screen.findByRole('button', { name: /save rotation/i });
  fireEvent.click(save);
  expect(vi.mocked(viewer.savePageRotation)).toHaveBeenCalledWith(3);
});

test('the reset button clears a stored manual rotation', async () => {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => 'blob:corrected-url'),
    revokeObjectURL: vi.fn(),
  });
  viewer.document = documentSummary();
  viewer.tree = treeWithPage({ manual_rotation_deg: 90 });
  viewer.pageRotations = {};
  vi.mocked(getPageImage).mockResolvedValueOnce(
    new Blob(['png'], { type: 'image/png' })
  );

  render(
    <CorrectedPageView
      documentId="doc-1"
      pageNumber={3}
      fallbackAspectRatio={0.8}
    />
  );

  // A page with a stored override and no on-screen rotation offers a reset.
  const reset = await screen.findByRole('button', { name: /reset/i });
  fireEvent.click(reset);
  expect(vi.mocked(viewer.resetPageRotation)).toHaveBeenCalledWith(3);
});
