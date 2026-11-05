import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { AxiosError, AxiosHeaders } from 'axios';
import type { AdminNodeStatus, NodeStatusReport } from '@/lib/admin';

vi.mock('@/lib/admin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/admin')>();
  return { ...actual, getNodeStatus: vi.fn(), forgetNode: vi.fn() };
});

import {
  NODE_STATUS_POLL_INTERVAL_MS,
  SystemStatusPanel,
} from './SystemStatusPanel';
import { forgetNode, getNodeStatus } from '@/lib/admin';

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (
    this: HTMLDialogElement
  ) {
    this.open = true;
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.open = false;
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

const NOW = new Date().toISOString().replace('Z', '');

function node(overrides: Partial<AdminNodeStatus> = {}): AdminNodeStatus {
  return {
    id: 'n1',
    kind: 'worker',
    name: 'celery@worker-1',
    hostname: 'worker-1',
    url: null,
    status: 'healthy',
    latency_ms: 48,
    last_checked_at: NOW,
    last_seen_at: NOW,
    last_error: null,
    details: { active_tasks: 1, concurrency: 4, pipeline_version: null },
    ...overrides,
  };
}

function report(
  nodes: AdminNodeStatus[],
  overrides: Partial<NodeStatusReport> = {}
): NodeStatusReport {
  return {
    checked_at: NOW,
    overall_status: 'healthy',
    summary: { healthy: nodes.length, degraded: 0, down: 0, unknown: 0 },
    nodes,
    ...overrides,
  };
}

const GATEWAY = node({
  id: 'g1',
  kind: 'inference',
  name: 'gateway',
  hostname: 'gateway',
  url: 'http://gateway:8080/healthz',
  latency_ms: 12,
  details: { http_status: 200, model_name: null, model_version: null },
});

const SLOW = node({
  id: 'n2',
  name: 'celery@worker-2',
  hostname: 'worker-2',
  status: 'degraded',
  latency_ms: 1840,
  last_error: 'Slow response (1840 ms)',
});

const GONE = node({
  id: 'n3',
  name: 'celery@worker-3',
  hostname: 'worker-3',
  status: 'down',
  latency_ms: null,
  last_seen_at: '2026-11-04T14:51:37.220000',
  last_error: 'No response within 2.0s',
  details: {},
});

function row(name: string): HTMLTableRowElement {
  const cell = screen.getByText(name, { selector: 'span.break-all' });
  const tr = cell.closest('tr');
  if (!tr) throw new Error(`no row for ${name}`);
  return tr;
}

async function renderPanel(next: NodeStatusReport) {
  vi.mocked(getNodeStatus).mockResolvedValue(next);
  render(<SystemStatusPanel />);
  await waitFor(() => expect(screen.getByRole('table')).toBeTruthy());
}

it('shows each node with health, hostname, latency and details', async () => {
  await renderPanel(report([GATEWAY, node()]));
  expect(getNodeStatus).toHaveBeenCalledWith(false);
  expect(screen.getByText('All systems healthy')).toBeTruthy();
  expect(screen.getByText('Checked just now')).toBeTruthy();

  const worker = within(row('celery@worker-1'));
  expect(worker.getByText('Healthy')).toBeTruthy();
  expect(worker.getByText('Worker')).toBeTruthy();
  expect(worker.getByText('worker-1')).toBeTruthy();
  expect(worker.getByText('48 ms')).toBeTruthy();
  expect(worker.getByText('1 / 4 tasks')).toBeTruthy();

  const gateway = within(row('gateway'));
  expect(gateway.getByText('Inference server')).toBeTruthy();
  expect(gateway.getByText('12 ms')).toBeTruthy();
});

it('labels every status in text, not only color', async () => {
  await renderPanel(
    report([
      GATEWAY,
      SLOW,
      GONE,
      node({ id: 'n4', name: 'celery@new', status: 'unknown' }),
    ])
  );
  expect(within(row('gateway')).getByText('Healthy')).toBeTruthy();
  expect(within(row('celery@worker-2')).getByText('Degraded')).toBeTruthy();
  expect(within(row('celery@worker-3')).getByText('Down')).toBeTruthy();
  expect(within(row('celery@new')).getByText('Unknown')).toBeTruthy();
});

it('highlights degraded and down rows and explains why', async () => {
  await renderPanel(
    report([GATEWAY, SLOW, GONE], { overall_status: 'degraded' })
  );
  expect(
    screen.getByText('Degraded', { selector: 'span.text-slate-900' })
  ).toBeTruthy();

  const slow = row('celery@worker-2');
  expect(slow.dataset.status).toBe('degraded');
  expect(slow.cells[0].className).toContain('border-l-amber-400');
  expect(within(slow).getByText('Slow response (1840 ms)')).toBeTruthy();

  const gone = row('celery@worker-3');
  expect(gone.dataset.status).toBe('down');
  expect(gone.cells[0].className).toContain('border-l-red-500');
  expect(within(gone).getByText('No response within 2.0s')).toBeTruthy();
  expect(within(gone).getAllByText('-', { selector: 'td' })).toHaveLength(2);

  const healthy = row('gateway');
  expect(healthy.cells[0].className).not.toContain('border-l-4');
});

it('offers remove only for down workers', async () => {
  await renderPanel(
    report([
      { ...GATEWAY, status: 'down', last_error: 'Connection refused' },
      node(),
      GONE,
    ])
  );
  expect(screen.getAllByRole('button', { name: /^Remove/ })).toHaveLength(1);
  expect(
    screen.getByRole('button', { name: 'Remove celery@worker-3' })
  ).toBeTruthy();
});

it('removes a down worker after confirm', async () => {
  await renderPanel(report([node(), GONE]));
  vi.mocked(forgetNode).mockResolvedValue();
  vi.mocked(getNodeStatus).mockResolvedValue(report([node()]));

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove celery@worker-3' })
  );
  const dialog = screen.getByRole('dialog', { name: 'Remove this worker?' });
  expect(
    within(dialog).getByText(/celery@worker-3 will no longer/)
  ).toBeTruthy();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));

  await waitFor(() => expect(forgetNode).toHaveBeenCalledWith('n3'));
  await waitFor(() => expect(screen.queryByText('celery@worker-3')).toBeNull());
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('keeps the dialog open and shows the error when remove fails', async () => {
  await renderPanel(report([node(), GONE]));
  vi.mocked(forgetNode).mockRejectedValue(
    apiError(409, { detail: 'Only a node that is down can be removed' })
  );

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove celery@worker-3' })
  );
  const dialog = screen.getByRole('dialog', { name: 'Remove this worker?' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));

  expect(
    await within(dialog).findByText('Only a node that is down can be removed')
  ).toBeTruthy();
  expect(
    within(dialog).getByRole('button', { name: 'Try again' })
  ).toBeTruthy();
});

it('cancel closes the dialog without removing', async () => {
  await renderPanel(report([GONE]));
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove celery@worker-3' })
  );
  const dialog = screen.getByRole('dialog', { name: 'Remove this worker?' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(forgetNode).not.toHaveBeenCalled();
});

it('check now bypasses the cache', async () => {
  await renderPanel(report([node()]));
  vi.mocked(getNodeStatus).mockResolvedValue(
    report(
      [{ ...node(), status: 'down', last_error: 'Message broker unreachable' }],
      {
        overall_status: 'down',
      }
    )
  );
  fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
  await waitFor(() => expect(getNodeStatus).toHaveBeenLastCalledWith(true));
  expect(await screen.findByText('Message broker unreachable')).toBeTruthy();
});

it('polls while visible', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  await renderPanel(report([node()]));
  expect(getNodeStatus).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(NODE_STATUS_POLL_INTERVAL_MS);
  });
  expect(getNodeStatus).toHaveBeenCalledTimes(2);
});

it('says administrator access is needed on 403', async () => {
  vi.mocked(getNodeStatus).mockRejectedValue(
    apiError(403, { detail: "The user doesn't have enough privileges" })
  );
  render(<SystemStatusPanel />);
  expect(await screen.findByText('You need administrator access')).toBeTruthy();
});

it('shows an empty state before any node reports', async () => {
  vi.mocked(getNodeStatus).mockResolvedValue(
    report([], { overall_status: 'down' })
  );
  render(<SystemStatusPanel />);
  expect(
    await screen.findByText(
      'No worker nodes or inference servers have reported yet.'
    )
  ).toBeTruthy();
});

function apiError(status: number, data: unknown): AxiosError {
  const headers = new AxiosHeaders();
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, null, {
    status,
    statusText: '',
    headers,
    config: { headers },
    data,
  });
}
