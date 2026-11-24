/**
 * The example PDF offered to first-time users (US-116).
 *
 * The file ships with the frontend (public/samples) and holds only made-up
 * content. It goes through the same upload call as any other file, so it is
 * stored in the user's own library, counts toward their storage and is
 * processed by the normal pipeline.
 */

import { announceBatchActivity, sealBatch } from './batchNotifications';
import {
  newBatchId,
  newUploadId,
  uploadDocument,
  type UploadOutcome,
} from './documents';

export const SAMPLE_DOCUMENT_URL = '/samples/yarrow-sample-document.pdf';
export const SAMPLE_DOCUMENT_FILENAME = 'yarrow-sample-document.pdf';

/** Fetch the bundled example and upload it as a one-file batch. */
export async function uploadSampleDocument(): Promise<UploadOutcome> {
  let file: File;
  try {
    const res = await fetch(SAMPLE_DOCUMENT_URL);
    if (!res.ok) throw new Error(`status ${res.status}`);
    file = new File([await res.blob()], SAMPLE_DOCUMENT_FILENAME, {
      type: 'application/pdf',
    });
  } catch {
    return {
      kind: 'failed',
      message: 'The sample document could not be loaded. Please try again.',
    };
  }

  const batchId = newBatchId();
  // allowDuplicate: trying the sample a second time (e.g. after deleting the
  // first copy) should just work rather than stop on the duplicate warning.
  const outcome = await uploadDocument(
    file,
    newUploadId(),
    () => {},
    batchId,
    true
  );
  if (outcome.kind === 'accepted') {
    // No more files are coming, so the batch can be announced when it is done.
    await sealBatch(batchId).catch(() => {});
    announceBatchActivity();
  }
  return outcome;
}
