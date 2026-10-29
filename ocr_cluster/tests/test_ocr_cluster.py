"""Assertions for a RUNNING OCR cluster stack (non-VLM services).

The heavy lifting is elsewhere: the images are built (by CI steps or a
developer's compose), and the services are started with
``docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --wait``,
whose healthchecks gate each service on its own health endpoint:

    paddlex-pipeline, paddleocr-vl-api, hybrid-gateway, gateway

This test stays small on purpose and only asserts what compose healthchecks
cannot see: the edge gateway's behavior from outside. The sglang/vllm
``vlm-server`` is excluded from the stack under test (it needs a real GPU and
a multi-GB model pull); the paddlex GenAI client is lazy, so the paddlex
services stay healthy with it absent.

Stack selection follows ``OCR_CLUSTER_STACK`` (``x86`` or ``dgx``; default
``x86``); it exists so the aarch64 job skips locally on an x86 host, exactly
as the build/run does.

Local runbook (x86, CPU-only so it does not touch a busy GPU; use a port
override if the developer stack already owns host port 8080):

    docker compose -p ocr-ci-x86 \\
        -f ocr_cluster/x86/docker-compose.yml \\
        -f ocr_cluster/x86/docker-compose.ci.yml \\
        up -d --wait

    OCR_CLUSTER_TEST=1 pytest ocr_cluster/tests -v -m ocr_cluster

    docker compose -p ocr-ci-x86 down -v --remove-orphans

Environment:
    OCR_CLUSTER_TEST    Set to run the assertions (otherwise skipped).
    OCR_CLUSTER_STACK   ``x86``, ``dgx``, or comma-separated (default ``x86``).
    OCR_BASE_URL        Edge gateway address (default ``http://127.0.0.1:8080``).
"""

import os
import platform
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.ocr_cluster


def _select_stacks() -> list:
    raw = os.environ.get("OCR_CLUSTER_STACK", "x86")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _host_arch() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in ("arm64", "aarch64") else "amd64"


def _get(url: str) -> tuple:
    """GET url; return (status, body) treating any HTTP error as its status."""
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


@pytest.mark.parametrize("stack", _select_stacks())
def test_ocr_cluster_edge(stack):
    if not os.environ.get("OCR_CLUSTER_TEST"):
        pytest.skip("Set OCR_CLUSTER_TEST=1 to assert a running OCR cluster stack.")
    if stack not in ("x86", "dgx"):
        pytest.fail(f"Unknown OCR_CLUSTER_STACK: {stack!r} (expected x86 or dgx)")
    if stack == "dgx" and _host_arch() != "arm64":
        pytest.skip("dgx (aarch64) results require an aarch64 host")

    base = os.environ.get("OCR_BASE_URL", "http://127.0.0.1:8080").rstrip("/")

    status, body = _get(f"{base}/healthz")
    assert status == 200, f"gateway /healthz returned {status}: {body!r}"
    assert "ok" in body, f"gateway /healthz unexpected body: {body!r}"

    status, _ = _get(base + "/")
    assert status == 401, (
        f"gateway / accepted an unauthenticated request (status {status})"
    )
