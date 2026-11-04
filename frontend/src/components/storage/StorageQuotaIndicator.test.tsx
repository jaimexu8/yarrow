import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import api from '@/lib/api';
import { deleteDocument, uploadDocument } from '@/lib/documents';
import type { StorageUsage } from '@/lib/storage';
import { StorageQuotaIndicator } from './StorageQuotaIndicator';

vi.mock('@/lib/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const GB = 1073741824;

function usage(overrides: Partial<StorageUsage> = {}): StorageUsage {
  return {
    used_bytes: 125829120,
    reserved_bytes: 0,
    trash_bytes: 0,
    quota_bytes: GB,
    remaining_bytes: 947912704,
    used_ratio: 0.1171875,
    level: 'ok',
    warning_threshold: 0.8,
    critical_threshold: 0.95,
    ...overrides,
  };
}

function respond(...values: StorageUsage[]) {
  for (const data of values) {
    vi.mocked(api.get).mockResolvedValueOnce({ data });
  }
}

async function progressbar() {
  return screen.findByRole('progressbar', { name: 'Storage used' });
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe('StorageQuotaIndicator', () => {
  it('shows the formatted usage text', async () => {
    respond(usage());
    render(<StorageQuotaIndicator />);

    expect(await screen.findByText('120 MB of 1 GB used')).toBeTruthy();
  });

  it('exposes usage as an accessible progress bar', async () => {
    respond(usage());
    render(<StorageQuotaIndicator />);

    const bar = await progressbar();
    expect(bar.getAttribute('aria-valuemin')).toBe('0');
    expect(bar.getAttribute('aria-valuemax')).toBe(String(GB));
    expect(bar.getAttribute('aria-valuenow')).toBe('125829120');
    expect(bar.getAttribute('aria-valuetext')).toBe('120 MB of 1 GB used');
  });

  it('counts reserved bytes in the bar and caps it at the quota', async () => {
    respond(usage({ used_bytes: GB - 100, reserved_bytes: 500 }));
    render(<StorageQuotaIndicator />);

    const bar = await progressbar();
    expect(bar.getAttribute('aria-valuenow')).toBe(String(GB));
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('100%');
  });

  it('sizes the fill by used and reserved bytes', async () => {
    respond(usage({ used_bytes: 250, reserved_bytes: 50, quota_bytes: 1000 }));
    render(<StorageQuotaIndicator />);

    const bar = await progressbar();
    expect(bar.getAttribute('aria-valuenow')).toBe('300');
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('30%');
  });

  it.each([
    ['ok', 'bg-slate-900'],
    ['warning', 'bg-amber-500'],
    ['critical', 'bg-red-600'],
    ['full', 'bg-red-600'],
  ] as const)('colors the %s fill with %s', async (level, color) => {
    respond(usage({ level }));
    render(<StorageQuotaIndicator />);

    const fill = (await progressbar()).firstElementChild as HTMLElement;
    expect(fill.classList.contains(color)).toBe(true);
  });

  it('links to the trash when it holds documents', async () => {
    respond(usage({ trash_bytes: 4194304 }));
    render(<StorageQuotaIndicator />);

    const link = await screen.findByRole('link', { name: '4 MB in trash' });
    expect(link.getAttribute('href')).toBe('/trash');
  });

  it('has no trash link when the trash is empty', async () => {
    respond(usage());
    render(<StorageQuotaIndicator />);

    await progressbar();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('shows a placeholder while loading', () => {
    vi.mocked(api.get).mockReturnValueOnce(new Promise(() => undefined));
    const { container } = render(<StorageQuotaIndicator />);

    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(container.firstElementChild?.getAttribute('aria-hidden')).toBe(
      'true'
    );
  });

  it('says so when usage cannot be loaded', async () => {
    vi.mocked(api.get).mockRejectedValueOnce(new Error('offline'));
    render(<StorageQuotaIndicator />);

    expect(
      await screen.findByText('Storage usage is unavailable right now.')
    ).toBeTruthy();
  });

  it('updates after a document is deleted', async () => {
    respond(usage(), usage({ used_bytes: 104857600 }));
    vi.mocked(api.delete).mockResolvedValueOnce({});
    render(<StorageQuotaIndicator />);
    await screen.findByText('120 MB of 1 GB used');

    await deleteDocument('doc-1');

    expect(await screen.findByText('100 MB of 1 GB used')).toBeTruthy();
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('updates after a document is uploaded', async () => {
    respond(usage(), usage({ used_bytes: 136314880 }));
    vi.mocked(api.post).mockResolvedValueOnce({
      data: {
        accepted: [
          { document_id: 'doc-1', filename: 'a.pdf', status: 'queued' },
        ],
        rejected: [],
      },
    });
    render(<StorageQuotaIndicator />);
    await screen.findByText('120 MB of 1 GB used');

    await uploadDocument(new File(['x'], 'a.pdf'), 'upload-1', () => undefined);

    await waitFor(() =>
      expect(
        (screen.getByRole('progressbar') as HTMLElement).getAttribute(
          'aria-valuetext'
        )
      ).toBe('130 MB of 1 GB used')
    );
  });
});
