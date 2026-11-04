'use client';

import { Alert } from '@/components/ui/Alert';
import { useStorageUsage } from '@/lib/useStorageUsage';

export function StorageQuotaBanner({ className }: { className?: string }) {
  const { usage } = useStorageUsage();

  if (!usage || usage.level === 'ok') return null;

  const percent = Math.floor(usage.used_ratio * 100);

  if (usage.level === 'warning') {
    return (
      <Alert tone="warning" className={className}>
        {`Your storage is ${percent}% full. Delete documents or empty the trash to free space.`}
      </Alert>
    );
  }

  return (
    <Alert tone="error" className={className}>
      {usage.level === 'critical'
        ? `Your storage is almost full (${percent}% used).`
        : 'Your storage is full. New uploads will be rejected until you free space.'}
    </Alert>
  );
}
