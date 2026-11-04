'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Spinner } from '@/components/ui/Spinner';
import {
  forgetNode,
  getNodeStatus,
  NODE_HEALTH_LABELS,
  NODE_KIND_LABELS,
  nodeDetails,
  nodeLatency,
  relativeTime,
  type AdminNodeStatus,
  type NodeHealth,
  type NodeStatusReport,
} from '@/lib/admin';
import { cn } from '@/lib/cn';
import { parseApiDate } from '@/lib/documents';
import { toApiError } from '@/lib/errors';

export const NODE_STATUS_POLL_INTERVAL_MS = 15000;

const CLOCK_TICK_MS = 5000;

const FORBIDDEN_MESSAGE = 'You need administrator access';

const CELL =
  'border-y border-slate-200 bg-white px-3 py-3 sm:px-4 align-middle first:rounded-l-xl first:border-l last:rounded-r-xl last:border-r';
const HEAD = 'px-3 pb-1 text-left sm:px-4 text-xs font-medium text-slate-500';

const BADGE_STYLES: Record<NodeHealth, string> = {
  healthy: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  degraded: 'border-amber-300 bg-amber-50 text-amber-800',
  down: 'border-red-300 bg-red-50 text-red-800',
  unknown: 'border-slate-300 bg-slate-100 text-slate-700',
};

const DOT_STYLES: Record<NodeHealth, string> = {
  healthy: 'bg-emerald-500',
  degraded: 'bg-amber-500',
  down: 'bg-red-500',
  unknown: 'bg-slate-400',
};

const ROW_EDGE: Partial<Record<NodeHealth, string>> = {
  degraded: 'first:border-l-4 first:border-l-amber-400',
  down: 'first:border-l-4 first:border-l-red-500',
};

const OVERALL_LABELS: Record<NodeStatusReport['overall_status'], string> = {
  healthy: 'All systems healthy',
  degraded: 'Degraded',
  down: 'Down',
};

function errorMessage(err: unknown): string {
  const apiError = toApiError(err);
  return apiError.status === 403 ? FORBIDDEN_MESSAGE : apiError.message;
}

function HealthBadge({ status }: { status: NodeHealth }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium',
        BADGE_STYLES[status]
      )}
    >
      <span
        aria-hidden="true"
        className={cn('size-1.5 rounded-full', DOT_STYLES[status])}
      />
      {NODE_HEALTH_LABELS[status]}
    </span>
  );
}

function LastSeen({ value, now }: { value: string | null; now: Date }) {
  if (!value) return <>-</>;
  const date = parseApiDate(value);
  return (
    <time dateTime={date.toISOString()} title={date.toLocaleString()}>
      {relativeTime(date, now)}
    </time>
  );
}

function NodeRow({
  node,
  now,
  onRemove,
}: {
  node: AdminNodeStatus;
  now: Date;
  onRemove: (node: AdminNodeStatus) => void;
}) {
  const edge = ROW_EDGE[node.status];
  const removable = node.kind === 'worker' && node.status === 'down';
  return (
    <tr data-status={node.status}>
      <td className={cn(CELL, edge)}>
        <HealthBadge status={node.status} />
      </td>
      <td className={cn(CELL, 'text-slate-700')}>
        {NODE_KIND_LABELS[node.kind]}
      </td>
      <td className={CELL}>
        <span className="break-all font-medium text-slate-900">
          {node.name}
        </span>
        {node.status === 'down' && node.last_error && (
          <p className="mt-0.5 text-xs text-red-700">{node.last_error}</p>
        )}
        {node.status === 'degraded' && node.last_error && (
          <p className="mt-0.5 text-xs text-amber-800">{node.last_error}</p>
        )}
      </td>
      <td className={cn(CELL, 'break-all text-slate-700')}>{node.hostname}</td>
      <td className={cn(CELL, 'tabular-nums text-slate-700')}>
        {nodeLatency(node)}
      </td>
      <td className={cn(CELL, 'whitespace-nowrap text-slate-600')}>
        <LastSeen value={node.last_seen_at} now={now} />
      </td>
      <td className={cn(CELL, 'tabular-nums text-slate-600')}>
        {nodeDetails(node)}
      </td>
      <td className={cn(CELL, 'text-right')}>
        {removable && (
          <Button
            variant="secondary"
            className="w-auto px-3 py-1.5"
            onClick={() => onRemove(node)}
            aria-label={`Remove ${node.name}`}
          >
            Remove
          </Button>
        )}
      </td>
    </tr>
  );
}

function NodeTable({
  nodes,
  now,
  onRemove,
}: {
  nodes: AdminNodeStatus[];
  now: Date;
  onRemove: (node: AdminNodeStatus) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[56rem] border-separate border-spacing-y-2 text-sm">
        <thead>
          <tr>
            <th className={HEAD}>Status</th>
            <th className={HEAD}>Type</th>
            <th className={HEAD}>Name</th>
            <th className={HEAD}>Hostname</th>
            <th className={HEAD}>Latency</th>
            <th className={HEAD}>Last seen</th>
            <th className={HEAD}>Details</th>
            <th className={cn(HEAD, 'text-right')}>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((node) => (
            <NodeRow key={node.id} node={node} now={now} onRemove={onRemove} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SystemStatusPanel() {
  const [report, setReport] = useState<NodeStatusReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const [nodeToRemove, setNodeToRemove] = useState<AdminNodeStatus | null>(
    null
  );
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const alive = useRef(true);

  const load = useCallback(async (refresh: boolean) => {
    try {
      const next = await getNodeStatus(refresh);
      if (!alive.current) return;
      setReport(next);
      setError(null);
      setNow(new Date());
    } catch (err) {
      if (!alive.current) return;
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      clearTimeout(timer);
      if (document.visibilityState !== 'visible') return;
      await load(false);
      if (!alive.current) return;
      clearTimeout(timer);
      timer = setTimeout(poll, NODE_STATUS_POLL_INTERVAL_MS);
    }

    function onVisibility() {
      if (document.visibilityState === 'visible') void poll();
      else clearTimeout(timer);
    }

    void poll();
    const clock = setInterval(() => setNow(new Date()), CLOCK_TICK_MS);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      alive.current = false;
      clearTimeout(timer);
      clearInterval(clock);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [load]);

  async function checkNow() {
    setRefreshing(true);
    await load(true);
    if (alive.current) setRefreshing(false);
  }

  async function confirmRemove() {
    if (!nodeToRemove) return;
    setRemoving(true);
    setRemoveError(null);
    try {
      await forgetNode(nodeToRemove.id);
      if (!alive.current) return;
      setReport((current) =>
        current
          ? {
              ...current,
              nodes: current.nodes.filter(
                (node) => node.id !== nodeToRemove.id
              ),
            }
          : current
      );
      setNodeToRemove(null);
      await load(false);
    } catch (err) {
      if (alive.current) setRemoveError(errorMessage(err));
    } finally {
      if (alive.current) setRemoving(false);
    }
  }

  return (
    <section className="space-y-3" aria-labelledby="system-status-heading">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-0.5">
          <h2
            id="system-status-heading"
            className="text-lg font-semibold text-slate-900"
          >
            System status
          </h2>
          {report && (
            <p className="flex items-center gap-2 text-sm text-slate-600">
              <span
                aria-hidden="true"
                className={cn(
                  'size-2 rounded-full',
                  DOT_STYLES[report.overall_status]
                )}
              />
              <span className="font-medium text-slate-900">
                {OVERALL_LABELS[report.overall_status]}
              </span>
              <span aria-hidden="true">·</span>
              <span>
                Checked {relativeTime(parseApiDate(report.checked_at), now)}
              </span>
            </p>
          )}
        </div>
        <Button
          variant="secondary"
          className="w-auto px-3 py-1.5"
          onClick={checkNow}
          loading={refreshing}
        >
          {!refreshing && <RefreshCw className="size-4" aria-hidden="true" />}
          Check now
        </Button>
      </div>
      {error && <Alert>{error}</Alert>}
      {!report && !error && (
        <div className="flex justify-center py-6 text-slate-500">
          <Spinner label="Loading system status" />
        </div>
      )}
      {report && report.nodes.length === 0 && (
        <p className="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-600">
          No worker nodes or inference servers have reported yet.
        </p>
      )}
      {report && report.nodes.length > 0 && (
        <NodeTable
          nodes={report.nodes}
          now={now}
          onRemove={(node) => {
            setRemoveError(null);
            setNodeToRemove(node);
          }}
        />
      )}
      <Modal
        open={nodeToRemove !== null}
        onClose={() => {
          if (!removing) setNodeToRemove(null);
        }}
        dismissible={!removing}
        title="Remove this worker?"
        footer={
          <>
            <Button
              variant="secondary"
              className="sm:w-auto"
              onClick={() => setNodeToRemove(null)}
              disabled={removing}
              data-autofocus
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              className="sm:w-auto"
              onClick={confirmRemove}
              loading={removing}
            >
              {removeError ? 'Try again' : 'Remove'}
            </Button>
          </>
        }
      >
        <p className="break-words">
          {nodeToRemove?.name} will no longer be listed. If it comes back
          online, it will appear again at the next check.
        </p>
        {removeError && (
          <Alert className="mt-4" tone="error">
            {removeError}
          </Alert>
        )}
      </Modal>
    </section>
  );
}
