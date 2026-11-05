import asyncio
import time
import uuid
from datetime import datetime
from typing import Self

import httpx
import pytest
import respx
from kombu.exceptions import OperationalError
from yarrow_db.models import ServiceNode, User

from app.core.config import settings
from app.schemas.admin import AdminNodeStatus
from app.services import node_health

NODES = "/api/v1/admin/nodes"
GATEWAY = "http://gateway.test:8080/healthz"
SECRET = "hunter2-secret-detail"


class FakeConnection:
    def __init__(self, celery: "FakeCelery") -> None:
        self.celery = celery

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def ensure_connection(self, **_kwargs) -> None:
        if self.celery.broker_down:
            raise OperationalError(f"Error 111 connecting to valkey: {SECRET}")


class FakeInspect:
    def __init__(self, celery: "FakeCelery", destination: list[str]) -> None:
        self.celery = celery
        self.destination = destination

    def active(self) -> dict:
        return {
            host: [{"id": str(n)} for n in range(self.celery.active.get(host, 0))]
            for host in self.destination
        }

    def stats(self) -> dict:
        return {
            host: {"pool": {"max-concurrency": self.celery.concurrency}}
            for host in self.destination
        }


class FakeControl:
    def __init__(self, celery: "FakeCelery") -> None:
        self.celery = celery

    def broadcast(self, command, reply, timeout, callback, connection):
        assert command == "ping"
        assert reply is True
        assert timeout == settings.WORKER_PING_TIMEOUT_SECONDS
        self.celery.pings += 1
        start = time.monotonic()
        for host, delay in self.celery.replies:
            remaining = start + delay - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            callback({host: {"ok": "pong"}})
        return [{host: {"ok": "pong"}} for host, _ in self.celery.replies]

    def inspect(self, destination, timeout, limit, connection):
        return FakeInspect(self.celery, destination)


class FakeCelery:
    def __init__(self) -> None:
        self.replies: list[tuple[str, float]] = [("celery@worker-1", 0.0)]
        self.active: dict[str, int] = {"celery@worker-1": 1}
        self.concurrency = 4
        self.broker_down = False
        self.pings = 0
        self.control = FakeControl(self)

    def connection_for_write(self) -> FakeConnection:
        return FakeConnection(self)


@pytest.fixture
def celery(monkeypatch: pytest.MonkeyPatch) -> FakeCelery:
    fake = FakeCelery()
    monkeypatch.setattr(node_health, "get_celery", lambda: fake)
    return fake


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        settings, "INFERENCE_HEALTH_TARGETS", [{"name": "gateway", "url": GATEWAY}]
    )
    with respx.mock(assert_all_called=False) as router:
        router.get(GATEWAY).mock(return_value=httpx.Response(200, text="ok\n"))
        yield router


@pytest.fixture
async def admin_headers(client, auth_headers, db_session) -> dict[str, str]:
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    user = await db_session.get(User, uuid.UUID(me["id"]))
    user.is_admin = True
    await db_session.flush()
    return auth_headers


async def _check(client, headers, refresh: bool = True) -> dict:
    response = await client.get(
        NODES, headers=headers, params={"refresh": str(refresh).lower()}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _node(report: dict, name: str) -> dict:
    return next(node for node in report["nodes"] if node["name"] == name)


class TestAccess:
    async def test_unauthenticated_gets_401(self, client):
        assert (await client.get(NODES)).status_code == 401
        assert (await client.delete(f"{NODES}/{uuid.uuid4()}")).status_code == 401

    async def test_non_admin_gets_403_on_get(self, client, auth_headers, celery):
        response = await client.get(NODES, headers=auth_headers)
        assert response.status_code == 403
        assert response.json() == {"detail": "The user doesn't have enough privileges"}
        assert celery.pings == 0

    async def test_non_admin_gets_403_on_delete(self, client, auth_headers, db_session):
        node = ServiceNode(
            kind="worker", name="celery@gone", hostname="gone", status="down"
        )
        db_session.add(node)
        await db_session.flush()
        response = await client.delete(f"{NODES}/{node.id}", headers=auth_headers)
        assert response.status_code == 403
        assert await db_session.get(ServiceNode, node.id) is not None


class TestWorkers:
    async def test_healthy_worker_reports_hostname_latency_and_details(
        self, client, admin_headers, celery, gateway
    ):
        report = await _check(client, admin_headers)
        worker = _node(report, "celery@worker-1")
        assert worker["kind"] == "worker"
        assert worker["hostname"] == "worker-1"
        assert worker["url"] is None
        assert worker["status"] == "healthy"
        assert isinstance(worker["latency_ms"], int)
        assert worker["last_error"] is None
        assert worker["last_seen_at"] == worker["last_checked_at"]
        assert worker["details"] == {
            "active_tasks": 1,
            "concurrency": 4,
            "pipeline_version": None,
        }

    async def test_each_worker_gets_its_own_latency(
        self, client, admin_headers, celery, gateway
    ):
        celery.replies = [("celery@fast", 0.0), ("celery@slow", 0.3)]
        report = await _check(client, admin_headers)
        fast = _node(report, "celery@fast")["latency_ms"]
        slow = _node(report, "celery@slow")["latency_ms"]
        assert fast < 150
        assert slow >= 300

    async def test_slow_worker_is_degraded(
        self, client, admin_headers, celery, gateway, monkeypatch
    ):
        monkeypatch.setattr(settings, "WORKER_DEGRADED_LATENCY_MS", 200)
        celery.replies = [("celery@fast", 0.0), ("celery@slow", 0.3)]
        report = await _check(client, admin_headers)
        slow = _node(report, "celery@slow")
        assert slow["status"] == "degraded"
        assert slow["last_error"] == f"Slow response ({slow['latency_ms']} ms)"
        assert _node(report, "celery@fast")["status"] == "healthy"
        assert report["overall_status"] == "degraded"

    async def test_missing_worker_is_down_and_keeps_last_seen(
        self, client, admin_headers, celery, gateway
    ):
        celery.replies = [("celery@worker-1", 0.0), ("celery@worker-2", 0.0)]
        first = await _check(client, admin_headers)
        seen = _node(first, "celery@worker-2")["last_seen_at"]

        celery.replies = [("celery@worker-1", 0.0)]
        second = await _check(client, admin_headers)
        gone = _node(second, "celery@worker-2")
        assert gone["status"] == "down"
        assert gone["last_seen_at"] == seen
        assert gone["last_checked_at"] == second["checked_at"]
        assert gone["last_error"] == "No response within 2.0s"
        assert gone["latency_ms"] is None
        assert gone["details"] == {}
        assert second["summary"] == {
            "healthy": 2,
            "degraded": 0,
            "down": 1,
            "unknown": 0,
        }
        assert second["overall_status"] == "degraded"

    async def test_broker_down_marks_every_known_worker_down(
        self, client, admin_headers, celery, gateway
    ):
        celery.replies = [("celery@worker-1", 0.0), ("celery@worker-2", 0.0)]
        await _check(client, admin_headers)

        celery.broker_down = True
        response = await client.get(
            NODES, headers=admin_headers, params={"refresh": "true"}
        )
        assert response.status_code == 200
        assert SECRET not in response.text
        report = response.json()
        for name in ("celery@worker-1", "celery@worker-2"):
            node = _node(report, name)
            assert node["status"] == "down"
            assert node["last_error"] == "Message broker unreachable"
        assert report["overall_status"] == "down"

    async def test_worker_that_replies_is_added(
        self, client, admin_headers, celery, gateway
    ):
        await _check(client, admin_headers)
        celery.replies = [("celery@worker-1", 0.0), ("celery@new", 0.0)]
        report = await _check(client, admin_headers)
        assert _node(report, "celery@new")["status"] == "healthy"


class TestInference:
    async def test_healthy_gateway(self, client, admin_headers, celery, gateway):
        report = await _check(client, admin_headers)
        node = _node(report, "gateway")
        assert node["kind"] == "inference"
        assert node["hostname"] == "gateway.test"
        assert node["url"] == GATEWAY
        assert node["status"] == "healthy"
        assert isinstance(node["latency_ms"], int)
        assert node["details"] == {
            "http_status": 200,
            "model_name": None,
            "model_version": None,
        }
        assert report["overall_status"] == "healthy"
        assert report["nodes"][0]["kind"] == "inference"

    async def test_model_reported_by_gateway(
        self, client, admin_headers, celery, gateway
    ):
        gateway.get(GATEWAY).mock(
            return_value=httpx.Response(
                200, json={"model_name": "layout", "model_version": "1.2"}
            )
        )
        node = _node(await _check(client, admin_headers), "gateway")
        assert node["details"]["model_name"] == "layout"
        assert node["details"]["model_version"] == "1.2"

    async def test_slow_gateway_is_degraded(
        self, client, admin_headers, celery, gateway, monkeypatch
    ):
        monkeypatch.setattr(settings, "INFERENCE_DEGRADED_LATENCY_MS", 50)

        async def slow(_request):
            await asyncio.sleep(0.1)
            return httpx.Response(200, text="ok\n")

        gateway.get(GATEWAY).mock(side_effect=slow)
        node = _node(await _check(client, admin_headers), "gateway")
        assert node["status"] == "degraded"
        assert node["latency_ms"] >= 100
        assert node["last_error"] == f"Slow response ({node['latency_ms']} ms)"

    async def test_timeout_is_down(self, client, admin_headers, celery, gateway):
        gateway.get(GATEWAY).mock(side_effect=httpx.ReadTimeout(SECRET))
        response = await client.get(
            NODES, headers=admin_headers, params={"refresh": "true"}
        )
        node = _node(response.json(), "gateway")
        assert node["status"] == "down"
        assert node["last_error"] == "Timed out after 3.0s"
        assert node["latency_ms"] is None
        assert SECRET not in response.text

    async def test_closed_port_is_refused(
        self, client, admin_headers, celery, monkeypatch
    ):
        monkeypatch.setattr(
            settings,
            "INFERENCE_HEALTH_TARGETS",
            [{"name": "closed", "url": "http://127.0.0.1:1/healthz"}],
        )
        node = _node(await _check(client, admin_headers), "closed")
        assert node["status"] == "down"
        assert node["last_error"] == "Connection refused"
        assert node["hostname"] == "127.0.0.1"

    async def test_unknown_host_is_unreachable(
        self, client, admin_headers, celery, gateway
    ):
        gateway.get(GATEWAY).mock(
            side_effect=httpx.ConnectError(f"[Errno -2] Name not known {SECRET}")
        )
        response = await client.get(
            NODES, headers=admin_headers, params={"refresh": "true"}
        )
        node = _node(response.json(), "gateway")
        assert node["last_error"] == "Host unreachable"
        assert SECRET not in response.text

    async def test_non_200_is_down(self, client, admin_headers, celery, gateway):
        await _check(client, admin_headers)
        gateway.get(GATEWAY).mock(return_value=httpx.Response(503, text=SECRET))
        response = await client.get(
            NODES, headers=admin_headers, params={"refresh": "true"}
        )
        node = _node(response.json(), "gateway")
        assert node["status"] == "down"
        assert node["last_error"] == "HTTP 503"
        assert node["details"]["http_status"] == 503
        assert node["last_seen_at"] == node["last_checked_at"]
        assert SECRET not in response.text

    async def test_target_derived_from_inference_service_url(
        self, client, admin_headers, celery, monkeypatch
    ):
        monkeypatch.setattr(settings, "INFERENCE_HEALTH_TARGETS", [])
        monkeypatch.setattr(
            settings, "INFERENCE_SERVICE_URL", "http://infer.test:9090/layout-parsing"
        )
        with respx.mock() as router:
            route = router.get("http://infer.test:9090/healthz").mock(
                return_value=httpx.Response(200, text="ok\n")
            )
            node = _node(await _check(client, admin_headers), "infer.test")
        assert route.called
        assert node["hostname"] == "infer.test"
        assert node["status"] == "healthy"

    async def test_unconfigured_target_is_removed(
        self, client, admin_headers, celery, gateway, monkeypatch
    ):
        await _check(client, admin_headers)
        other = "http://other.test/healthz"
        gateway.get(other).mock(return_value=httpx.Response(200))
        monkeypatch.setattr(
            settings, "INFERENCE_HEALTH_TARGETS", [{"name": "other", "url": other}]
        )
        report = await _check(client, admin_headers)
        names = [
            node["name"] for node in report["nodes"] if node["kind"] == "inference"
        ]
        assert names == ["other"]


class TestCache:
    async def test_recent_check_is_reused(self, client, admin_headers, celery, gateway):
        first = await _check(client, admin_headers, refresh=False)
        second = await _check(client, admin_headers, refresh=False)
        assert celery.pings == 1
        assert gateway.calls.call_count == 1
        assert second == first

    async def test_refresh_bypasses_cache(self, client, admin_headers, celery, gateway):
        await _check(client, admin_headers, refresh=False)
        await _check(client, admin_headers, refresh=True)
        assert celery.pings == 2
        assert gateway.calls.call_count == 2

    async def test_stale_check_is_repeated(
        self, client, admin_headers, celery, gateway, monkeypatch
    ):
        monkeypatch.setattr(settings, "NODE_STATUS_CACHE_SECONDS", 0)
        await _check(client, admin_headers, refresh=False)
        await _check(client, admin_headers, refresh=False)
        assert celery.pings == 2


class TestOrderAndOverall:
    async def test_nodes_ordered_by_kind_status_then_name(
        self, client, admin_headers, celery, gateway, monkeypatch
    ):
        monkeypatch.setattr(settings, "WORKER_DEGRADED_LATENCY_MS", 200)
        celery.replies = [
            ("celery@b", 0.0),
            ("celery@a", 0.0),
            ("celery@gone", 0.0),
        ]
        await _check(client, admin_headers)
        celery.replies = [("celery@b", 0.0), ("celery@a", 0.0), ("celery@z", 0.3)]
        report = await _check(client, admin_headers)
        assert [node["name"] for node in report["nodes"]] == [
            "gateway",
            "celery@gone",
            "celery@z",
            "celery@a",
            "celery@b",
        ]

    async def test_no_workers_is_down(self, client, admin_headers, celery, gateway):
        celery.replies = []
        report = await _check(client, admin_headers)
        assert report["overall_status"] == "down"

    def _status(self, kind: str, status: str) -> AdminNodeStatus:
        return AdminNodeStatus(
            id=uuid.uuid4(),
            kind=kind,
            name=f"{kind}-{status}",
            hostname="h",
            status=status,
        )

    def test_overall_status_rules(self):
        healthy = [
            self._status("worker", "healthy"),
            self._status("inference", "healthy"),
        ]
        assert node_health.overall_status(healthy) == "healthy"
        assert (
            node_health.overall_status([*healthy, self._status("worker", "down")])
            == "degraded"
        )
        assert (
            node_health.overall_status(
                [
                    self._status("worker", "degraded"),
                    self._status("inference", "healthy"),
                ]
            )
            == "degraded"
        )
        assert (
            node_health.overall_status(
                [self._status("worker", "down"), self._status("inference", "healthy")]
            )
            == "down"
        )
        assert (
            node_health.overall_status(
                [
                    self._status("worker", "healthy"),
                    self._status("inference", "unknown"),
                ]
            )
            == "down"
        )
        assert node_health.overall_status([]) == "down"


class TestForget:
    async def _add(self, db_session, **values) -> ServiceNode:
        node = ServiceNode(
            hostname="host", last_checked_at=datetime(2026, 1, 1), **values
        )
        db_session.add(node)
        await db_session.flush()
        return node

    async def test_down_worker_is_removed(self, client, admin_headers, db_session):
        node = await self._add(
            db_session, kind="worker", name="celery@gone", status="down"
        )
        response = await client.delete(f"{NODES}/{node.id}", headers=admin_headers)
        assert response.status_code == 204
        db_session.expunge(node)
        assert await db_session.get(ServiceNode, node.id) is None

    async def test_live_worker_is_not_removed(self, client, admin_headers, db_session):
        node = await self._add(
            db_session, kind="worker", name="celery@up", status="healthy"
        )
        response = await client.delete(f"{NODES}/{node.id}", headers=admin_headers)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "NODE_NOT_DOWN"

    async def test_inference_node_is_configured(
        self, client, admin_headers, db_session
    ):
        node = await self._add(
            db_session, kind="inference", name="gateway", status="down"
        )
        response = await client.delete(f"{NODES}/{node.id}", headers=admin_headers)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "NODE_CONFIGURED"

    async def test_unknown_node_is_404(self, client, admin_headers):
        response = await client.delete(f"{NODES}/{uuid.uuid4()}", headers=admin_headers)
        assert response.status_code == 404
        assert response.json() == {
            "detail": {"detail": "Node not found", "code": "NODE_NOT_FOUND"}
        }
