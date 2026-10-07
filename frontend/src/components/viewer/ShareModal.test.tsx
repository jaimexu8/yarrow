import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { ShareModal } from './ShareModal';
import * as sharingLib from '@/lib/sharing';

vi.mock('@/lib/sharing', () => ({
  listDocumentShares: vi.fn(),
  shareDocument: vi.fn(),
  revokeDocumentShare: vi.fn(),
  updateSharePermission: vi.fn(),
}));

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (
    this: HTMLDialogElement
  ) {
    this.open = true;
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.open = false;
  });
});

afterEach(cleanup);

describe('ShareModal component', () => {
  it('renders modal when open is true and displays shared list', async () => {
    vi.mocked(sharingLib.listDocumentShares).mockResolvedValueOnce([
      {
        id: 's1',
        document_id: 'doc-1',
        shared_with_user_id: 'u2',
        shared_with_email: 'collaborator@example.com',
        shared_with_name: 'Alice',
        permission: 'review',
        created_at: null,
      },
    ]);

    render(
      <ShareModal
        documentId="doc-1"
        documentTitle="Quarterly Report.pdf"
        open={true}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText('Share Document')).toBeDefined();
    expect(screen.getByText('Quarterly Report.pdf')).toBeDefined();

    const collaborator = await screen.findByText('collaborator@example.com');
    expect(collaborator).toBeDefined();
    expect(screen.getByText('Can review')).toBeDefined();
  });

  it('renders empty message when no shares exist', async () => {
    vi.mocked(sharingLib.listDocumentShares).mockResolvedValueOnce([]);

    render(
      <ShareModal
        documentId="doc-1"
        documentTitle="Doc"
        open={true}
        onClose={vi.fn()}
      />
    );

    const emptyText = await screen.findByText(
      'This document hasn’t been shared with anyone yet.'
    );
    expect(emptyText).toBeDefined();
  });
});
