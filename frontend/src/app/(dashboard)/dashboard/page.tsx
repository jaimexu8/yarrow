'use client';

import { useEffect, useState } from 'react';

import { DocumentList } from '../../../components/documents/DocumentList';
import { Spinner } from '../../../components/ui/Spinner';
import { listDocuments, type Document } from '../../../lib/documents';

export default function DashboardPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await listDocuments();
        if (!cancelled) {
          setDocuments(data);
        }
      } catch {
        if (!cancelled) {
          setError(true);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto max-w-3xl px-4 py-8">
      <h1 className="mb-6 text-2xl font-semibold text-gray-900">Documents</h1>
      {loading ? (
        <Spinner />
      ) : error ? (
        <p className="text-red-600">Could not load documents.</p>
      ) : (
        <DocumentList documents={documents} />
      )}
    </main>
  );
}
