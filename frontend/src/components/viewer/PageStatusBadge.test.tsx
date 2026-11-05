import { afterEach, expect, test } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import type { PageStatus } from '@/lib/progress';
import { PageStatusBadge } from './PageStatusBadge';

afterEach(cleanup);

const CASES: [PageStatus, string, string][] = [
  ['queued', 'Queued', 'bg-slate-100'],
  ['processing', 'Processing', 'bg-sky-50'],
  ['completed', 'Completed', 'bg-emerald-50'],
  ['failed', 'Failed', 'bg-red-50'],
  ['canceled', 'Canceled', 'bg-slate-100'],
];

test.each(CASES)(
  'shows %s pages with their own label and tone',
  (status, label, tone) => {
    render(<PageStatusBadge status={status} />);
    const badge = screen.getByText(label);
    expect(badge.className).toContain(tone);
  }
);

test('gives every status a distinct label', () => {
  const labels = CASES.map(([status]) => {
    const { container, unmount } = render(<PageStatusBadge status={status} />);
    const text = container.textContent;
    unmount();
    return text;
  });
  expect(new Set(labels).size).toBe(CASES.length);
});

test('spins only while a page is processing', () => {
  const { container, rerender } = render(
    <PageStatusBadge status="processing" />
  );
  expect(container.querySelector('svg.animate-spin')).not.toBeNull();
  rerender(<PageStatusBadge status="queued" />);
  expect(container.querySelector('svg.animate-spin')).toBeNull();
});

test('renders nothing for a page without a status', () => {
  const { container } = render(<PageStatusBadge status={null} />);
  expect(container.innerHTML).toBe('');
});
