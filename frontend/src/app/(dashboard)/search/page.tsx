'use client';

import Link from 'next/link';
import { useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { toApiError } from '@/lib/errors';
import { documentHitHref, HighlightedText } from '@/lib/highlight';
import {
  searchDocuments,
  viewerHighlightTerm,
  isApproxMatch,
  type SearchHit,
} from '@/lib/search';

const DOCUMENT_TYPES = [
  { value: 'application/pdf', label: 'PDF' },
  { value: 'image/png', label: 'PNG' },
  { value: 'image/jpeg', label: 'JPEG' },
  { value: 'image/gif', label: 'GIF' },
];

export default function SearchPage() {
  const [query, setQuery] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [fileType, setFileType] = useState('');
  const [results, setResults] = useState<SearchHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submittedTerm, setSubmittedTerm] = useState('');

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const term = viewerHighlightTerm(query);
      setSubmittedTerm(term);
      setResults(
        await searchDocuments(query, {
          dateFrom: dateFrom || undefined,
          dateTo: dateTo || undefined,
          fileType: fileType || undefined,
        })
      );
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
          Find text in your processed documents. Leave the search box empty to
          browse by date or type.
        </p>
        <p className="text-xs text-slate-500">
          Wrap a phrase in double quotes to search for it exactly, e.g.
          &ldquo;quarterly summary&rdquo;. OCR near matches (1/l, 0/O, a small
          typo) still match.
        </p>
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
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
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Input
            id="search-date-from"
            label="From date"
            type="date"
            value={dateFrom}
            onChange={(event) => setDateFrom(event.target.value)}
            max={dateTo || undefined}
            invalidMessage="Must be on or before the end date."
          />
          <Input
            id="search-date-to"
            label="To date"
            type="date"
            value={dateTo}
            onChange={(event) => setDateTo(event.target.value)}
            min={dateFrom || undefined}
            invalidMessage="Must be on or after the start date."
          />
          <Select
            id="search-type"
            label="Document type"
            value={fileType}
            onChange={(event) => setFileType(event.target.value)}
          >
            <option value="">Any type</option>
            {DOCUMENT_TYPES.map((type) => (
              <option key={type.value} value={type.value}>
                {type.label}
              </option>
            ))}
          </Select>
        </div>
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
        <ul className="space-y-4">
          {results.map((hit) => (
            <li
              key={hit.id}
              className="rounded-xl border border-slate-200 bg-white"
            >
              <p className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-900">
                {hit.filename}
              </p>
              {hit.snippets.length === 0 ? (
                <Link
                  href={`/documents/${hit.id}`}
                  className="block px-4 py-3 text-sm text-slate-600 underline-offset-4 hover:bg-slate-50 hover:underline"
                >
                  Open document
                </Link>
              ) : (
                <ul>
                  {hit.snippets.map((snippet) => (
                    <li
                      key={snippet.region_id}
                      className="border-t border-slate-100 first:border-t-0"
                    >
                      <Link
                        href={documentHitHref(
                          hit.id,
                          snippet.region_id,
                          submittedTerm
                        )}
                        className="block px-4 py-3 hover:bg-slate-50"
                      >
                        <p className="text-xs font-medium text-slate-500">
                          Page {snippet.page_number}
                          {isApproxMatch(snippet, submittedTerm) && (
                            <span
                              title="Approximate match: the text differs slightly from your search (e.g. an OCR error like 1/l or 0/O)"
                              className="ml-1.5 rounded bg-sky-100 px-1.5 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-sky-800"
                            >
                              approx
                            </span>
                          )}
                          {snippet.edited && (
                            <span
                              title="This region was manually corrected"
                              className="ml-1.5 rounded bg-amber-100 px-1.5 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-amber-800"
                            >
                              corrected
                            </span>
                          )}
                        </p>
                        <p className="mt-1 text-sm text-slate-800">
                          <HighlightedText
                            text={snippet.text}
                            matchStart={snippet.match_start}
                            matchEnd={snippet.match_end}
                          />
                        </p>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
