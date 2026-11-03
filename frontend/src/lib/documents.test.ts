import { afterEach, expect, it, vi } from 'vitest';
import { TRASH_RETENTION_DAYS, trashDaysLeft } from './documents';

afterEach(() => {
  vi.useRealTimers();
});

it('keeps a fixed 7-day trash window', () => {
  expect(TRASH_RETENTION_DAYS).toBe(7);
});

it('counts whole days left in trash from deleted_at', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-01-08T00:00:00Z'));
  expect(trashDaysLeft('2026-01-01T00:00:00')).toBe(0);
  expect(trashDaysLeft('2026-01-02T00:00:00')).toBe(1);
  expect(trashDaysLeft('2026-01-07T00:00:00')).toBe(6);
});
