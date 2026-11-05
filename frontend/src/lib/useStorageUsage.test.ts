import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import api from './api';
import { notifyStorageChanged, type StorageUsage } from './storage';
import { useStorageUsage } from './useStorageUsage';

vi.mock('./api', () => ({ default: { get: vi.fn() } }));

function usage(used_bytes: number): StorageUsage {
  return {
    used_bytes,
    reserved_bytes: 0,
    trash_bytes: 0,
    quota_bytes: 1000,
    remaining_bytes: 1000 - used_bytes,
    used_ratio: used_bytes / 1000,
    level: 'ok',
    warning_threshold: 0.8,
    critical_threshold: 0.95,
  };
}

function respond(...values: (StorageUsage | Error)[]) {
  for (const value of values) {
    if (value instanceof Error) vi.mocked(api.get).mockRejectedValueOnce(value);
    else vi.mocked(api.get).mockResolvedValueOnce({ data: value });
  }
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe('useStorageUsage', () => {
  it('fetches on mount', async () => {
    respond(usage(100));
    const { result } = renderHook(() => useStorageUsage());

    expect(result.current).toEqual({ usage: null, failed: false });
    await waitFor(() => expect(result.current.usage).toEqual(usage(100)));
    expect(api.get).toHaveBeenCalledWith('/api/v1/auth/me/storage');
  });

  it('refetches when storage changes', async () => {
    respond(usage(100), usage(250));
    const { result } = renderHook(() => useStorageUsage());
    await waitFor(() => expect(result.current.usage?.used_bytes).toBe(100));

    act(() => notifyStorageChanged());

    await waitFor(() => expect(result.current.usage?.used_bytes).toBe(250));
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('refetches when the window regains focus', async () => {
    respond(usage(100), usage(300));
    const { result } = renderHook(() => useStorageUsage());
    await waitFor(() => expect(result.current.usage?.used_bytes).toBe(100));

    act(() => {
      window.dispatchEvent(new Event('focus'));
    });

    await waitFor(() => expect(result.current.usage?.used_bytes).toBe(300));
  });

  it('shares one request between components mounted together', async () => {
    respond(usage(100));
    const first = renderHook(() => useStorageUsage());
    const second = renderHook(() => useStorageUsage());

    await waitFor(() => expect(second.result.current.usage).not.toBeNull());
    expect(first.result.current.usage).toEqual(usage(100));
    expect(api.get).toHaveBeenCalledOnce();
  });

  it('keeps only the latest response when changes overlap', async () => {
    let finishStale: (value: { data: StorageUsage }) => void = () => undefined;
    vi.mocked(api.get)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          finishStale = resolve;
        })
      )
      .mockResolvedValueOnce({ data: usage(400) });
    const { result } = renderHook(() => useStorageUsage());

    act(() => notifyStorageChanged());
    await waitFor(() => expect(result.current.usage?.used_bytes).toBe(400));
    await act(async () => finishStale({ data: usage(100) }));

    expect(result.current.usage?.used_bytes).toBe(400);
  });

  it('keeps the last known usage when a refetch fails', async () => {
    respond(usage(100), new Error('offline'));
    const { result } = renderHook(() => useStorageUsage());
    await waitFor(() => expect(result.current.usage?.used_bytes).toBe(100));

    act(() => notifyStorageChanged());

    await waitFor(() => expect(result.current.failed).toBe(true));
    expect(result.current.usage?.used_bytes).toBe(100);
  });

  it('forgets usage and stops listening once nothing uses it', async () => {
    respond(usage(100), usage(200));
    const { result, unmount } = renderHook(() => useStorageUsage());
    await waitFor(() => expect(result.current.usage).not.toBeNull());

    unmount();
    notifyStorageChanged();
    expect(api.get).toHaveBeenCalledOnce();

    const remounted = renderHook(() => useStorageUsage());
    expect(remounted.result.current.usage).toBeNull();
    await waitFor(() =>
      expect(remounted.result.current.usage?.used_bytes).toBe(200)
    );
  });
});
