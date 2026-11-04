import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import api from '@/lib/api';
import {
  notifyStorageChanged,
  type StorageLevel,
  type StorageUsage,
} from '@/lib/storage';
import { StorageQuotaBanner } from './StorageQuotaBanner';

vi.mock('@/lib/api', () => ({ default: { get: vi.fn() } }));

function usage(level: StorageLevel, used_ratio: number): StorageUsage {
  return {
    used_bytes: Math.round(used_ratio * 1000),
    reserved_bytes: 0,
    trash_bytes: 0,
    quota_bytes: 1000,
    remaining_bytes: Math.max(1000 - Math.round(used_ratio * 1000), 0),
    used_ratio,
    level,
    warning_threshold: 0.8,
    critical_threshold: 0.95,
  };
}

function respond(...values: StorageUsage[]) {
  for (const data of values) {
    vi.mocked(api.get).mockResolvedValueOnce({ data });
  }
}

async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe('StorageQuotaBanner', () => {
  it('stays hidden while storage is ok', async () => {
    respond(usage('ok', 0.79));
    const { container } = render(<StorageQuotaBanner />);
    await settle();

    expect(api.get).toHaveBeenCalledOnce();
    expect(container.innerHTML).toBe('');
  });

  it('warns politely at the warning level', async () => {
    respond(usage('warning', 0.8379));
    render(<StorageQuotaBanner />);

    const banner = await screen.findByRole('status');
    expect(banner.textContent).toBe(
      'Your storage is 83% full. Delete documents or empty the trash to free space.'
    );
    expect(banner.className).toContain('bg-amber-50');
  });

  it('alerts at the critical level', async () => {
    respond(usage('critical', 0.9699));
    render(<StorageQuotaBanner />);

    const banner = await screen.findByRole('alert');
    expect(banner.textContent).toBe('Your storage is almost full (96% used).');
    expect(banner.className).toContain('bg-red-50');
  });

  it('alerts when storage is full', async () => {
    respond(usage('full', 1.02));
    render(<StorageQuotaBanner />);

    const banner = await screen.findByRole('alert');
    expect(banner.textContent).toBe(
      'Your storage is full. New uploads will be rejected until you free space.'
    );
    expect(banner.className).toContain('bg-red-50');
  });

  it('cannot be dismissed', async () => {
    respond(usage('critical', 0.97));
    render(<StorageQuotaBanner />);

    await screen.findByRole('alert');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('goes away once space is freed', async () => {
    respond(usage('warning', 0.85), usage('ok', 0.5));
    render(<StorageQuotaBanner />);
    await screen.findByRole('status');

    act(() => notifyStorageChanged());

    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(api.get).toHaveBeenCalledTimes(2);
  });
});
