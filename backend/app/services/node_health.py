import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import ServiceNode

from app.core.config import settings
from app.core.queue import get_celery
from app.schemas.admin import AdminNodeStatus, NodeStatusReport, NodeStatusSummary

logger = logging.getLogger(__name__)

KIND_ORDER = {"inference": 0, "worker": 1}
STATUS_ORDER = {"down": 0, "degraded": 1, "unknown": 2, "healthy": 3}
UP_STATUSES = ("healthy", "degraded")
BROKER_UNREACHABLE = "Message broker unreachable"
CONNECTION_REFUSED = "Connection refused"
HOST_UNREACHABLE = "Host unreachable"


@dataclass(frozen=True)
class InferenceTarget:
    name: str
    url: str


@dataclass
class WorkerProbe:
    broker_ok: bool
    latencies: dict[str, float] = field(default_factory=dict)
    active_tasks: dict[str, int] = field(default_factory=dict)
    concurrency: dict[str, int] = field(default_factory=dict)
    pipeline_versions: dict[str, str | None] = field(default_factory=dict)


@dataclass
class InferenceProbe:
    target: InferenceTarget
    status: str
    latency_ms: int | None = None
    answered: bool = False
    last_error: str | None = None
    details: dict[str, int | str | None] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _host_of_url(url: str) -> str:
    return urlsplit(url).hostname or ""


def _host_of_worker(name: str) -> str:
    return name.split("@", 1)[1] if "@" in name else name


def _seconds(value: float) -> str:
    return f"{float(value)}s"


def _slow(latency_ms: int) -> str:
    return f"Slow response ({latency_ms} ms)"


def inference_targets() -> list[InferenceTarget]:
    configured = [
        InferenceTarget(name=str(item["name"]), url=str(item["url"]))
        for item in settings.INFERENCE_HEALTH_TARGETS
        if item.get("name") and item.get("url")
    ]
    if configured:
        return configured
    parts = urlsplit(settings.INFERENCE_SERVICE_URL)
    if not parts.hostname:
        return []
    url = urlunsplit((parts.scheme, parts.netloc, "/healthz", "", ""))
    return [InferenceTarget(name=parts.hostname, url=url)]


def _probe_workers() -> WorkerProbe:
    timeout = settings.WORKER_PING_TIMEOUT_SECONDS
    app = get_celery()
    probe = WorkerProbe(broker_ok=True)
    try:
        with app.connection_for_write() as connection:
            connection.ensure_connection(
                max_retries=1, interval_start=0, interval_step=0, timeout=timeout
            )
            start = time.monotonic()

            def on_reply(body: dict) -> None:
                elapsed = time.monotonic() - start
                for hostname in body:
                    probe.latencies.setdefault(hostname, elapsed)

            app.control.broadcast(
                "ping",
                reply=True,
                timeout=timeout,
                callback=on_reply,
                connection=connection,
            )
            if not probe.latencies:
                return probe
            hostnames = list(probe.latencies)
            inspect = app.control.inspect(
                destination=hostnames,
                timeout=timeout,
                limit=len(hostnames),
                connection=connection,
            )
            active = inspect.active() or {}
            stats = inspect.stats() or {}
    except Exception:
        if probe.latencies:
            logger.warning("Worker inspect failed after ping", exc_info=True)
            return probe
        logger.warning("Worker health check: broker unreachable", exc_info=True)
        return WorkerProbe(broker_ok=False)
    for hostname in hostnames:
        if isinstance(active.get(hostname), list):
            probe.active_tasks[hostname] = len(active[hostname])
        worker_stats = stats.get(hostname) or {}
        pool = worker_stats.get("pool") or {}
        if isinstance(pool.get("max-concurrency"), int):
            probe.concurrency[hostname] = pool["max-concurrency"]
        version = worker_stats.get("pipeline_version")
        probe.pipeline_versions[hostname] = (
            version if isinstance(version, str) else None
        )
    return probe


def _connection_error(exc: httpx.ConnectError) -> str:
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        cause = pending.pop()
        if id(cause) in seen:
            continue
        seen.add(id(cause))
        if isinstance(cause, ConnectionRefusedError):
            return CONNECTION_REFUSED
        pending.extend(
            linked
            for linked in (cause.__cause__, cause.__context__)
            if linked is not None
        )
    return HOST_UNREACHABLE


def _model_details(response: httpx.Response) -> tuple[str | None, str | None]:
    try:
        body = response.json()
    except ValueError:
        return None, None
    if not isinstance(body, dict):
        return None, None
    name = body.get("model_name")
    version = body.get("model_version")
    return (
        name if isinstance(name, str) else None,
        version if isinstance(version, str) else None,
    )


async def _probe_inference(
    client: httpx.AsyncClient, target: InferenceTarget
) -> InferenceProbe:
    timeout = settings.INFERENCE_HEALTH_TIMEOUT_SECONDS
    empty = {"http_status": None, "model_name": None, "model_version": None}
    start = time.monotonic()
    try:
        response = await client.get(target.url)
    except httpx.TimeoutException:
        return InferenceProbe(
            target,
            "down",
            last_error=f"Timed out after {_seconds(timeout)}",
            details=empty,
        )
    except httpx.ConnectError as exc:
        return InferenceProbe(
            target, "down", last_error=_connection_error(exc), details=empty
        )
    except httpx.HTTPError:
        logger.warning(
            "Inference health check failed for %s", target.name, exc_info=True
        )
        return InferenceProbe(
            target, "down", last_error=HOST_UNREACHABLE, details=empty
        )
    latency_ms = round((time.monotonic() - start) * 1000)
    if response.status_code != 200:
        return InferenceProbe(
            target,
            "down",
            answered=True,
            last_error=f"HTTP {response.status_code}",
            details={**empty, "http_status": response.status_code},
        )
    model_name, model_version = _model_details(response)
    details = {
        "http_status": response.status_code,
        "model_name": model_name,
        "model_version": model_version,
    }
    if latency_ms > settings.INFERENCE_DEGRADED_LATENCY_MS:
        return InferenceProbe(
            target,
            "degraded",
            latency_ms=latency_ms,
            answered=True,
            last_error=_slow(latency_ms),
            details=details,
        )
    return InferenceProbe(
        target, "healthy", latency_ms=latency_ms, answered=True, details=details
    )


async def _probe_all_inference(
    targets: list[InferenceTarget],
) -> list[InferenceProbe]:
    if not targets:
        return []
    async with httpx.AsyncClient(
        timeout=settings.INFERENCE_HEALTH_TIMEOUT_SECONDS
    ) as client:
        return list(
            await asyncio.gather(
                *(_probe_inference(client, target) for target in targets)
            )
        )


async def _upsert(db: AsyncSession, values: dict) -> None:
    update = {
        key: value
        for key, value in values.items()
        if key not in ("kind", "name", "created_at")
        and not (key == "last_seen_at" and value is None)
    }
    stmt = insert(ServiceNode).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_service_nodes_kind_name", set_=update
    )
    await db.execute(stmt)


async def _write_workers(db: AsyncSession, probe: WorkerProbe, now: datetime) -> None:
    timeout = settings.WORKER_PING_TIMEOUT_SECONDS
    for hostname, elapsed in probe.latencies.items():
        latency_ms = round(elapsed * 1000)
        degraded = latency_ms > settings.WORKER_DEGRADED_LATENCY_MS
        details: dict[str, int | str | None] = {
            "active_tasks": probe.active_tasks.get(hostname, 0),
            "concurrency": probe.concurrency.get(hostname, 0),
            "pipeline_version": probe.pipeline_versions.get(hostname),
        }
        await _upsert(
            db,
            {
                "kind": "worker",
                "name": hostname,
                "hostname": _host_of_worker(hostname),
                "url": None,
                "status": "degraded" if degraded else "healthy",
                "latency_ms": latency_ms,
                "last_checked_at": now,
                "last_seen_at": now,
                "last_error": _slow(latency_ms) if degraded else None,
                "details": details,
                "created_at": now,
                "updated_at": now,
            },
        )
    missing = (
        f"No response within {_seconds(timeout)}"
        if probe.broker_ok
        else BROKER_UNREACHABLE
    )
    silent = (
        await db.execute(
            select(ServiceNode).where(
                ServiceNode.kind == "worker",
                ServiceNode.name.not_in(list(probe.latencies)),
            )
        )
    ).scalars()
    for node in silent:
        node.status = "down"
        node.latency_ms = None
        node.last_checked_at = now
        node.last_error = missing
        node.details = {}
        node.updated_at = now


async def _write_inference(
    db: AsyncSession, probes: list[InferenceProbe], now: datetime
) -> None:
    for probe in probes:
        await _upsert(
            db,
            {
                "kind": "inference",
                "name": probe.target.name,
                "hostname": _host_of_url(probe.target.url),
                "url": probe.target.url,
                "status": probe.status,
                "latency_ms": probe.latency_ms,
                "last_checked_at": now,
                "last_seen_at": now if probe.answered else None,
                "last_error": probe.last_error,
                "details": probe.details,
                "created_at": now,
                "updated_at": now,
            },
        )
    await db.execute(
        delete(ServiceNode).where(
            ServiceNode.kind == "inference",
            ServiceNode.name.not_in([probe.target.name for probe in probes]),
        )
    )


def _sort_key(node: ServiceNode) -> tuple[int, int, str]:
    return (
        KIND_ORDER.get(node.kind, len(KIND_ORDER)),
        STATUS_ORDER.get(node.status, len(STATUS_ORDER)),
        node.name,
    )


def overall_status(nodes: list[AdminNodeStatus]) -> str:
    for kind in ("worker", "inference"):
        if not any(node.kind == kind and node.status in UP_STATUSES for node in nodes):
            return "down"
    if any(node.status in ("degraded", "down") for node in nodes):
        return "degraded"
    return "healthy"


async def _report(db: AsyncSession, checked_at: datetime) -> NodeStatusReport:
    rows = sorted(
        (
            await db.execute(
                select(ServiceNode).execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all(),
        key=_sort_key,
    )
    nodes = [AdminNodeStatus.model_validate(row) for row in rows]
    counts = dict.fromkeys(STATUS_ORDER, 0)
    for node in nodes:
        counts[node.status] += 1
    return NodeStatusReport(
        checked_at=checked_at,
        overall_status=overall_status(nodes),
        summary=NodeStatusSummary(**counts),
        nodes=nodes,
    )


async def check_nodes(db: AsyncSession, refresh: bool = False) -> NodeStatusReport:
    if not refresh:
        newest = (
            await db.execute(select(func.max(ServiceNode.last_checked_at)))
        ).scalar_one_or_none()
        cache_for = timedelta(seconds=settings.NODE_STATUS_CACHE_SECONDS)
        if newest is not None and _utcnow() - newest < cache_for:
            return await _report(db, newest)
    targets = inference_targets()
    workers, inference = await asyncio.gather(
        run_in_threadpool(_probe_workers), _probe_all_inference(targets)
    )
    now = _utcnow()
    await _write_workers(db, workers, now)
    await _write_inference(db, inference, now)
    await db.commit()
    return await _report(db, now)
