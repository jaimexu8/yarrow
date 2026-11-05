import api from './api';

export type StorageLevel = 'ok' | 'warning' | 'critical' | 'full';

export type StorageUsage = {
  used_bytes: number;
  reserved_bytes: number;
  trash_bytes: number;
  quota_bytes: number;
  remaining_bytes: number;
  used_ratio: number;
  level: StorageLevel;
  warning_threshold: number;
  critical_threshold: number;
};

export const STORAGE_CHANGED_EVENT = 'yarrow:storage-changed';

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

export async function getStorageUsage(): Promise<StorageUsage> {
  const res = await api.get<StorageUsage>('/api/v1/auth/me/storage');
  return res.data;
}

export function formatBytes(bytes: number): string {
  let value = Math.max(bytes, 0);
  let unit = 0;
  while (unit < BYTE_UNITS.length - 1 && Math.round(value) >= 1024) {
    value /= 1024;
    unit += 1;
  }
  const amount = value < 10 ? Number(value.toFixed(1)) : Math.round(value);
  return `${amount} ${BYTE_UNITS[unit]}`;
}

export function notifyStorageChanged(): void {
  window.dispatchEvent(new CustomEvent(STORAGE_CHANGED_EVENT));
}
