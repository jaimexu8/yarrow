import Link from 'next/link';
import { Plus } from 'lucide-react';
import { DocumentLibrary } from '@/components/documents/DocumentTable';

export default function DashboardPage() {
  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Documents
          </h1>
          <p className="text-sm text-slate-600">
            Manage uploaded files and review processing results.
          </p>
        </div>
        <Link
          href="/upload"
          className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          <Plus aria-hidden="true" className="size-4" />
          Upload documents
        </Link>
      </div>
      <DocumentLibrary />
    </div>
  );
}
