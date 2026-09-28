import type { Document } from '../../lib/documents';

import { DocumentCard } from './DocumentCard';

export function DocumentList({ documents }: { documents: Document[] }) {
  if (documents.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-gray-300 px-4 py-8 text-center text-gray-500">
        No documents yet.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {documents.map((document) => (
        <li key={document.id}>
          <DocumentCard document={document} />
        </li>
      ))}
    </ul>
  );
}
