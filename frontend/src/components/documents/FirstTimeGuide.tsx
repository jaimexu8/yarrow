'use client';

import { useState } from 'react';
import Link from 'next/link';
import { FileText, ScanText, Table2, Upload } from 'lucide-react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { uploadSampleDocument } from '@/lib/sampleDocument';
import { ACCEPTED_TYPES_TEXT } from '@/lib/uploads';

const STEPS = [
  {
    icon: Upload,
    title: 'Upload',
    text: `Add scanned ${ACCEPTED_TYPES_TEXT} files, one at a time or as a whole folder.`,
  },
  {
    icon: ScanText,
    title: 'Processing',
    text: 'Yarrow reads each page and finds its text, headings and tables. The status column shows how far along it is.',
  },
  {
    icon: Table2,
    title: 'Review and export',
    text: 'Open a finished document to see the results next to the original, fix anything that was misread, and export it.',
  },
];

/**
 * What a library with no documents shows (US-116): the three steps of using
 * Yarrow, a link to the upload page, and a button that uploads a bundled
 * example so a new user can see a result before using a file of their own.
 *
 * ``onSampleUploaded`` is called once the example is in the library, so the
 * caller can reload its list; the guide then goes away with the empty state.
 * It is told whether processing started, and the server's note if it did not.
 */
export function FirstTimeGuide({
  onSampleUploaded,
}: {
  onSampleUploaded: (sample: {
    processingStarted: boolean;
    message?: string;
  }) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function trySample() {
    setBusy(true);
    setError(null);
    const outcome = await uploadSampleDocument();
    if (outcome.kind === 'accepted') {
      // Stays busy: the guide is replaced by the library once it reloads.
      onSampleUploaded({
        processingStarted: outcome.processingStarted,
        message: outcome.message,
      });
      return;
    }
    setError(
      outcome.kind === 'failed'
        ? outcome.message
        : `The sample document was not added. ${outcome.reason}`
    );
    setBusy(false);
  }

  return (
    <section
      aria-labelledby="first-time-guide-title"
      className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-10"
    >
      <div className="mx-auto max-w-3xl">
        <div className="flex flex-col items-center text-center">
          <span className="flex size-11 items-center justify-center rounded-full bg-amber-50 text-amber-600">
            <FileText aria-hidden="true" className="size-5" />
          </span>
          <h2
            id="first-time-guide-title"
            className="mt-4 text-base font-semibold text-slate-900"
          >
            Get started with Yarrow
          </h2>
          <p className="mt-1 max-w-md text-sm text-slate-600">
            You don&apos;t have any documents yet. Yarrow turns scanned pages
            into text and tables you can search, correct and export.
          </p>
        </div>

        <ol className="mt-8 grid gap-4 sm:grid-cols-3">
          {STEPS.map((step, index) => (
            <li
              key={step.title}
              className="rounded-lg border border-slate-200 bg-slate-50 p-4"
            >
              <div className="flex items-center gap-2">
                <step.icon
                  aria-hidden="true"
                  className="size-4 text-slate-500"
                />
                <h3 className="text-sm font-semibold text-slate-900">
                  <span className="sr-only">Step </span>
                  {index + 1}. {step.title}
                </h3>
              </div>
              <p className="mt-2 text-sm text-slate-600">{step.text}</p>
            </li>
          ))}
        </ol>

        <div className="mt-8 flex flex-col items-center gap-3">
          <div className="flex flex-wrap items-center justify-center gap-3">
            <Link
              href="/upload"
              className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
            >
              <Upload aria-hidden="true" className="size-4" />
              Upload documents
            </Link>
            <Button
              variant="secondary"
              className="w-auto"
              loading={busy}
              onClick={trySample}
              aria-describedby="sample-document-hint"
            >
              {busy ? 'Adding sample document…' : 'Try sample document'}
            </Button>
          </div>
          <p
            id="sample-document-hint"
            className="max-w-md text-center text-xs text-slate-500"
          >
            The sample is a one-page order summary from a made-up company. It is
            added to your library and processed like any other upload.
          </p>
          {error && (
            <Alert tone="error" className="w-full max-w-md">
              {error}
            </Alert>
          )}
        </div>
      </div>
    </section>
  );
}
