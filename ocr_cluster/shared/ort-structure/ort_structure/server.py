"""FastAPI HTTP server for the ORT structure service.

This module handles Bearer auth, image decode,
bounded concurrency, per-request deadlines, and structured logging. It contains
no inference logic. Every page is handed to the pure ``Pipeline``
(which owns the model sessions) from a worker thread, so the event loop stays
responsive while a page is being processed.

Endpoints:
  POST /layout-parsing  {"file": <b64 png>, "fileType": 1}
      -> the PaddleX ``prunedResult`` envelope (worker contract)
  GET  /health          shallow liveness; never loads models
  GET  /ready           200 once the structure models are loaded, else 503

Auth: /layout-parsing requires ``Authorization: Bearer <STRUCTURE_API_KEY>``.
A missing or wrong key is a 401. /health and /ready are unauthenticated so the
compose healthcheck can probe them.

Concurrency: a bounded semaphore (``STRUCTURE_MAX_INFLIGHT``) caps in-flight
pages. A request that cannot get a slot within ``STRUCTURE_QUEUE_TIMEOUT`` is
rejected 503 with ``Retry-After``  A request that
gets a slot but exceeds ``REQUEST_TIMEOUT`` is cancelled 504 (retriable with
long backoff). Both free the semaphore on the way out.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from PIL import Image, UnidentifiedImageError

from ort_structure.pipeline import Pipeline

logger = logging.getLogger("ort_structure.server")


class LayoutParsingRequest(BaseModel):
    file: str
    fileType: int = 1


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def create_app(
    pipeline: Optional[Pipeline] = None,
    api_key: Optional[str] = None,
    max_inflight: int = 2,
    queue_timeout: float = 120.0,
    request_timeout: float = 280.0,
) -> FastAPI:
    """Build the app. ``pipeline`` is injected so tests can use a CPU or a
    stubbed pipeline; production passes None and the app loads one from the
    environment at startup (lifespan).

    The semaphore is created here (app-creation time, not import time) so its
    event-loop binding is per-app-instance.
    """
    api_key = (api_key if api_key is not None else _env("STRUCTURE_API_KEY", "")).strip()
    state = {"pipeline": pipeline, "load_error": None}
    sem = asyncio.Semaphore(max_inflight)

    async def _load() -> Pipeline:
        """Lazily construct the pipeline (model load). The heavy load_sessions
        runs in a worker thread so the event loop is not blocked during load."""
        if state["pipeline"] is not None:
            return state["pipeline"]
        if state["load_error"] is not None:
            raise state["load_error"]
        try:
            state["pipeline"] = await asyncio.to_thread(
                Pipeline,
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                device_id=int(_env("CUDA_DEVICE_ID", "0")),
                assert_gpu=_env("ORT_ASSERT_GPU", "1") == "1",
            )
        except Exception as e:  # noqa: BLE001
            state["load_error"] = e
            raise
        return state["pipeline"]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Preload so /ready reflects true readiness. A load failure is recorded
        # and surfaced as 503, not a crash (the compose healthcheck retries
        # through start_period). Skipped when a pipeline is injected (tests).
        if pipeline is None:
            try:
                await _load()
            except Exception as e:  # noqa: BLE001
                logger.error("structure model load failed at startup: %s", e)
        yield

    app = FastAPI(title="ORT structure service", lifespan=lifespan)
    app.state.structure = state

    def _authorized(request: Request) -> bool:
        if not api_key:
            # Auth is required on /layout-parsing; an unset key means the
            # service is misconfigured
            return False
        supplied = request.headers.get("authorization", "")
        if supplied.lower().startswith("bearer "):
            supplied = supplied.split(" ", 1)[1].strip()
        return supplied == api_key

    @app.get("/health")
    async def health() -> dict:
        # Shallow liveness. Never touches the models: a process that is up is
        # "healthy" even while models are still loading
        return {"ok": True}

    @app.get("/ready")
    async def ready() -> Response:
        try:
            await _load()
        except Exception as e:  # noqa: BLE001
            logger.error("not ready: %s", e)
            return JSONResponse(status_code=503, content={"ready": False, "error": str(e)})
        return JSONResponse(status_code=200, content={"ready": True})

    @app.post("/layout-parsing")
    async def layout_parsing(request: Request, req: LayoutParsingRequest):
        if req.fileType != 1:
            raise HTTPException(status_code=400, detail="only single images (fileType=1) are supported")
        if not _authorized(request):
            raise HTTPException(status_code=401, detail="missing or invalid Bearer key")

        # Decode at the boundary; a bad image is a permanent 400
        try:
            raw = base64.b64decode(req.file)
            arr = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"), dtype=np.uint8)
        except (UnidentifiedImageError, ValueError, binascii.Error) as e:
            raise HTTPException(status_code=400, detail=f"bad image: {e}")

        # Bounded concurrency: wait for a slot; if it does not free up in time,
        # reject 503 + Retry-After.
        try:
            await asyncio.wait_for(sem.acquire(), timeout=queue_timeout)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=503,
                headers={"Retry-After": "5"},
                detail="structure service saturated",
            )
        t0 = time.perf_counter()
        request_id = request.headers.get("x-request-id", "")
        try:
            # The pipeline may still be loading on first use; _load awaits it.
            p = await _load()
            # Run inference in a worker thread so the event loop (and /health,
            # /ready, other requests) stay responsive during a page.
            pruned, _elapsed = await asyncio.wait_for(
                asyncio.to_thread(p.process_page, arr), timeout=request_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "layout-parsing %s timed out after %.0fs", request_id or "-", request_timeout
            )
            raise HTTPException(status_code=504, detail="structure inference timed out")
        finally:
            sem.release()

        total_ms = (time.perf_counter() - t0) * 1000
        n_blocks = len(pruned.get("parsing_res_list", []))
        logger.info(
            "layout-parsing req=%s dims=%dx%d blocks=%d total_ms=%.0f",
            request_id or "-", arr.shape[1], arr.shape[0], n_blocks, total_ms,
        )
        return p.envelope(pruned)

    return app


app = create_app()
