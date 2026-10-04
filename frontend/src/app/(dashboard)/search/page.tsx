'use client';

import Link from 'next/link';
import { useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Spinner } from '@/components/ui/Spinner';
import { toApiError } from '@/lib/errors';
import { searchDocuments, type SearchHit } from '@/lib/search';

export default function SearchPage() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      setResults(await searchDocuments(query));
    } catch (err) {
      setResults(null);
      setError(toApiError(err).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          Search
        </h1>
        <p className="text-sm text-slate-600">
          Find text in your processed documents.
        </p>
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <Input
            id="search-q"
            label="Search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="a word from your documents"
            autoComplete="off"
          />
        </div>
        <Button type="submit" loading={loading} className="sm:w-auto">
          Search
        </Button>
      </form>

      {error && <Alert>{error}</Alert>}

      {loading && (
        <div className="flex justify-center py-8 text-slate-500">
          <Spinner label="Searching" />
        </div>
      )}

      {!loading && results && results.length === 0 && (
        <p className="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-600">
          No matching documents.
        </p>
      )}

      {!loading && results && results.length > 0 && (
        <ul className="divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white">
          {results.map((hit) => (
            <li key={hit.id}>
              <Link
                href={`/documents/${hit.id}`}
                className="block px-4 py-3 text-sm font-medium text-slate-900 underline-offset-4 hover:bg-slate-50 hover:underline"
              >
                {hit.filename}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
