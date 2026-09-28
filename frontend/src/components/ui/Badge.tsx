const STATUS_STYLES: Record<string, string> = {
  queued: 'bg-gray-100 text-gray-700',
  processing: 'bg-blue-100 text-blue-800',
  completed: 'bg-green-100 text-green-800',
  failed: 'bg-red-100 text-red-800',
  canceled: 'bg-yellow-100 text-yellow-800',
};

export function Badge({ status }: { status: string | null }) {
  const label = status ?? 'unknown';
  const style = STATUS_STYLES[label] ?? STATUS_STYLES.queued;

  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${style}`}
    >
      {label}
    </span>
  );
}
