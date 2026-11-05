import type { DocumentProgress, PageStatus } from './progress';
import type { PageNode } from './viewer';

export type PageState = {
  status: PageStatus | null;
  error_message: string | null;
};

const PAGE_STATUSES = new Set<string>([
  'queued',
  'processing',
  'completed',
  'failed',
  'canceled',
]);

function asPageStatus(status: string | null): PageStatus | null {
  return status !== null && PAGE_STATUSES.has(status)
    ? (status as PageStatus)
    : null;
}

export function mergePageStates(
  pages: PageNode[],
  progress: DocumentProgress | null
): Map<number, PageState> {
  const states = new Map<number, PageState>();
  for (const page of pages) {
    states.set(page.page_number, {
      status: asPageStatus(page.status),
      error_message: page.error_message,
    });
  }
  for (const page of progress?.pages ?? []) {
    states.set(page.page_number, {
      status: page.status,
      error_message: page.error_message,
    });
  }
  return states;
}
