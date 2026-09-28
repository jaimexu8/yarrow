import Link from 'next/link';

import type { Document } from '../../lib/documents';
import { Badge } from '../ui/Badge';

export function DocumentCard({ document }: { document: Document }) {
  return (
    <Link
      href={`/documents/${document.id}`}
      className="flex items-center justify-between rounded-lg border border-gray-200 px-4 py-3 hover:bg-gray-50"
    >
      <span className="truncate font-medium text-gray-900">
        {document.filename}
      </span>
      <Badge status={document.status} />
    </Link>
  );
}
