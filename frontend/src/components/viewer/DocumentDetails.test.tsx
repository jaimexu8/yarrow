import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import {
  getDocument,
  parseApiDate,
  type DocumentSummary,
  type ModelUsage,
} from '@/lib/documents';
import type { DocumentTree } from '@/lib/viewer';
import { DocumentDetails, ModelInfo } from './DocumentDetails';
import { ViewerProvider } from './ViewerContext';

vi.mock('@/lib/documents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/documents')>()),
  getDocument: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const NAME = 'PP-StructureV3+PaddleOCR-VL';

function viewerProps(fields: Partial<DocumentSummary>) {
  const document: DocumentSummary = {
    id: 'doc-1',
    filename: 'scan.pdf',
    file_size_bytes: 1024,
    file_type: 'application/pdf',
    page_count: 2,
    status: 'completed',
    error_message: null,
    created_at: '2026-11-01T09:00:00',
    folder_id: null,
    ...fields,
  };
  const tree = {
    document,
    stats: { page_count: 2, high_confidence_page_count: 2, warning_count: 0 },
    pages: [],
  } as unknown as DocumentTree;
  return { document, tree, refresh: () => {} };
}

function renderDetails(
  fields: Partial<DocumentSummary>,
  models: ModelUsage[] = []
) {
  const props = viewerProps(fields);
  vi.mocked(getDocument).mockResolvedValue({ ...props.document, models });

  render(
    <ViewerProvider {...props}>
      <DocumentDetails />
    </ViewerProvider>
  );
  fireEvent.click(screen.getByTitle('Document details'));
}

describe('DocumentDetails model information', () => {
  it('shows the model and when the document was processed', async () => {
    renderDetails(
      {
        model_name: NAME,
        model_version: '3.2.0+vl-0.9b',
        processed_at: '2026-11-04T15:02:20',
      },
      [{ model_name: NAME, model_version: '3.2.0+vl-0.9b', page_count: 2 }]
    );

    expect(screen.getByText(`${NAME} 3.2.0+vl-0.9b`)).toBeDefined();
    expect(
      screen.getByText(
        parseApiDate('2026-11-04T15:02:20').toLocaleString(undefined, {
          dateStyle: 'medium',
          timeStyle: 'short',
        })
      )
    ).toBeDefined();
    expect(getDocument).toHaveBeenCalledWith('doc-1');
    await Promise.resolve();
    expect(screen.queryByText(/Pages were processed by/)).toBeNull();
  });

  it('lists each version when pages were processed by several', async () => {
    renderDetails(
      {
        model_name: NAME,
        model_version: '3.2.0+vl-0.9b',
        processed_at: '2026-11-04T15:02:20',
      },
      [
        { model_name: NAME, model_version: '3.2.0+vl-0.9b', page_count: 11 },
        { model_name: NAME, model_version: '3.1.4+vl-0.9b', page_count: 1 },
      ]
    );

    expect(
      await screen.findByText('Pages were processed by 2 model versions')
    ).toBeDefined();
    expect(screen.getByText(`${NAME} 3.1.4+vl-0.9b`)).toBeDefined();
    expect(screen.getByText('11 pages')).toBeDefined();
    expect(screen.getByText('1 page')).toBeDefined();
  });

  it('shows Unknown when the model was never recorded', () => {
    renderDetails({
      model_name: null,
      model_version: null,
      processed_at: null,
    });

    expect(screen.getByText('Unknown')).toBeDefined();
    expect(screen.getByText('Not recorded')).toBeDefined();
  });
});

describe('ModelInfo', () => {
  function renderModelInfo(fields: Partial<DocumentSummary>) {
    render(
      <ViewerProvider {...viewerProps(fields)}>
        <ModelInfo />
      </ViewerProvider>
    );
  }

  it('shows the model name and version without opening anything', () => {
    renderModelInfo({ model_name: NAME, model_version: '3.2.0+vl-0.9b' });

    expect(screen.getByText(`Model: ${NAME} 3.2.0+vl-0.9b`)).toBeDefined();
    expect(screen.getByTitle(`Model: ${NAME} 3.2.0+vl-0.9b`)).toBeDefined();
  });

  it('shows Unknown for documents processed before models were recorded', () => {
    renderModelInfo({ model_name: null, model_version: null });

    expect(screen.getByText('Model: Unknown')).toBeDefined();
  });

  it('shows Unknown when the model fields are missing', () => {
    renderModelInfo({});

    expect(screen.getByText('Model: Unknown')).toBeDefined();
  });
});
