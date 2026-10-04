import type { ReactNode } from 'react';

const MARK = 'rounded-sm bg-amber-200 px-0.5 text-slate-900';

export function HighlightedText({
  text,
  query,
  matchStart,
  matchEnd,
}: {
  text: string;
  query?: string;
  matchStart?: number;
  matchEnd?: number;
}) {
  if (
    matchStart != null &&
    matchEnd != null &&
    matchStart >= 0 &&
    matchEnd > matchStart &&
    matchEnd <= text.length
  ) {
    return (
      <>
        {text.slice(0, matchStart)}
        <mark className={MARK}>{text.slice(matchStart, matchEnd)}</mark>
        {text.slice(matchEnd)}
      </>
    );
  }

  const term = query?.trim();
  if (!term) return text;

  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(escaped, 'gi');
  const nodes: ReactNode[] = [];
  let last = 0;
  let i = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    if (match[0].length === 0) {
      re.lastIndex += 1;
      continue;
    }
    if (match.index > last) nodes.push(text.slice(last, match.index));
    nodes.push(
      <mark key={i} className={MARK}>
        {match[0]}
      </mark>
    );
    last = match.index + match[0].length;
    i += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return <>{nodes}</>;
}

export function documentHitHref(
  documentId: string,
  regionId: string,
  query: string
): string {
  const params = new URLSearchParams({ region: regionId, q: query });
  return `/documents/${documentId}?${params.toString()}`;
}
