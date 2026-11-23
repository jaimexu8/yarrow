#!/usr/bin/env python3
"""Live load test for gateway concurrency.

Sends real page images to ``POST /layout-parsing`` at several concurrency
levels, records per-request latency/status/body hash, samples SGLang's
running-requests gauge at each level, and applies two gates:

* p95 latency at 4 concurrent pages must be well below 4x the single-page p95
  (threshold 2.5x), and
* the peak running-requests gauge must exceed 1 during the VLM stage.

Cross-page contamination check (weaker form). A requests-only script cannot
reliably make the OCR model read a synthetic marker drawn on a generated image,
so this uses distinct real page images instead of generated marker images. The
check asserts that the response bodies for distinct input images are distinct
and non-empty; it does not assert that a response contains its own marker and
excludes every other request's marker. Two identical bodies for different pages
are the signal this weaker check catches.

Depends only on the stdlib plus ``requests``. JSON goes to stdout; a short
human summary goes to stderr. Exit 0 on pass, 1 on fail.
"""

import argparse
import base64
import hashlib
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from probe_gateway_concurrency import (
    DEFAULT_API_KEY,
    DEFAULT_IMAGE_DIR,
    DEFAULT_METRICS_URL,
    DEFAULT_URL,
    choose_primary_gauge,
    pick_images,
    poll_metrics,
)

LATENCY_RATIO_THRESHOLD = 2.5
REQUIRED_LEVELS = (1, 4)


def post_and_hash(
    url: str, api_key: str, b64: str, index: int, image: str, timeout: float
) -> dict:
    """Send one page request and record latency, status, and a body hash."""
    headers = {"X-API-Key": api_key, "Authorization": f"Bearer {api_key}"}
    started = time.time()
    try:
        response = requests.post(
            url,
            headers=headers,
            json={"file": b64, "fileType": 1},
            timeout=timeout,
        )
        wall = time.time() - started
        return {
            "index": index,
            "image": image,
            "status": response.status_code,
            "wall_seconds": round(wall, 2),
            "body_sha256": hashlib.sha256(response.content).hexdigest(),
            "body_len": len(response.content),
            "body": response.text,
        }
    except Exception as exc:  # noqa: BLE001  # a failed request is a load-test result
        return {
            "index": index,
            "image": image,
            "status": None,
            "wall_seconds": round(time.time() - started, 2),
            "error": repr(exc),
        }


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def check_contamination(requests_result: list[dict]) -> dict:
    """Weak cross-page check: distinct pages must yield distinct, non-empty bodies."""
    ok = [item for item in requests_result if item["status"] == 200]
    hashes = [item.get("body_sha256", "") for item in ok]
    empty = [item["index"] for item in ok if not item.get("body_len")]
    duplicate_hashes = sorted({h for h in hashes if hashes.count(h) > 1})
    passed = len(ok) >= 2 and not empty and not duplicate_hashes
    detail = (
        f"{len(ok)} successful response(s); {len(set(hashes))} distinct body "
        f"hash(es); empty bodies at {empty or 'none'}; "
        f"duplicated hashes={len(duplicate_hashes)}"
    )
    if len(ok) < 2:
        detail += " (need >= 2 successful responses for this check)"
    return {"passed": passed, "detail": detail}


def run_level(
    level: int,
    requests_per_level: int,
    url: str,
    api_key: str,
    encoded: list[tuple[int, str, str]],
    metrics_url: str,
    poll_interval: float,
    timeout: float,
) -> dict:
    """Run one concurrency level, polling VLM metrics while requests fly."""
    samples: list = []
    discovered: set[str] = set()
    lock = threading.Lock()
    stop = threading.Event()
    poller = threading.Thread(
        target=poll_metrics,
        args=(metrics_url, poll_interval, stop, samples, discovered, lock),
        daemon=True,
    )

    poller.start()
    results: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=level) as pool:
            futures = [
                pool.submit(
                    post_and_hash,
                    url,
                    api_key,
                    encoded[idx][2],
                    idx,
                    encoded[idx][1],
                    timeout,
                )
                for idx in range(requests_per_level)
            ]
            for future in as_completed(futures):
                results.append(future.result())
    finally:
        stop.set()
        poller.join(timeout=5)

    results.sort(key=lambda item: item["index"])
    discovered_names = sorted(
        {line.split("{")[0].split()[0] for line in discovered}
    )
    primary = choose_primary_gauge(discovered_names)
    timeline: list[list[float]] = []
    if primary:
        for sample in samples:
            for item in sample.get("samples", []):
                if item["name"] == primary:
                    timeline.append([sample["t"], item["value"]])
    peak = max((value for _, value in timeline), default=0.0)

    latencies = [item["wall_seconds"] for item in results if item["status"] == 200]
    return {
        "level": level,
        "requests_sent": requests_per_level,
        "requests": [
            {k: v for k, v in item.items() if k != "body"} for item in results
        ],
        "p95_latency_seconds": round(p95(latencies), 2),
        "success_count": len(latencies),
        "primary_gauge": primary,
        "primary_gauge_peak": peak,
        "primary_gauge_timeline": timeline,
        "discovered_metric_names": discovered_names,
        "raw_metric_samples": samples,
        "contamination_check": check_contamination(results),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=DEFAULT_URL, help="layout-parsing endpoint")
    ap.add_argument("--api-key", default=DEFAULT_API_KEY)
    ap.add_argument(
        "--levels", default="1,2,4,8", help="comma-separated concurrency levels"
    )
    ap.add_argument(
        "--requests-per-level",
        type=int,
        default=0,
        help="requests sent at each level (0 = one per level of concurrency)",
    )
    ap.add_argument("--image-dir", default=DEFAULT_IMAGE_DIR)
    ap.add_argument("--metrics-url", default=DEFAULT_METRICS_URL)
    ap.add_argument("--poll-interval", type=float, default=0.25)
    ap.add_argument("--timeout", type=float, default=540, help="per-request timeout (s)")
    ap.add_argument(
        "--replicas",
        type=int,
        default=1,
        help="gateway replica count; recorded in output only (no containers created)",
    )
    args = ap.parse_args()

    try:
        levels = [int(part) for part in args.levels.split(",") if part.strip()]
    except ValueError as exc:
        raise SystemExit(f"invalid --levels: {exc}") from exc
    if not levels or any(level < 1 for level in levels):
        raise SystemExit("--levels must be a list of positive integers")
    if args.replicas < 1:
        raise SystemExit("--replicas must be >= 1")

    max_requests = max(
        args.requests_per_level if args.requests_per_level > 0 else level
        for level in levels
    )
    images = pick_images(args.image_dir, max_requests)
    encoded = [
        (idx, path.name, base64.b64encode(path.read_bytes()).decode("ascii"))
        for idx, path in enumerate(images)
    ]

    level_results = []
    for level in levels:
        count = args.requests_per_level if args.requests_per_level > 0 else level
        level_results.append(
            run_level(
                level,
                count,
                args.url,
                args.api_key,
                encoded,
                args.metrics_url,
                args.poll_interval,
                args.timeout,
            )
        )

    by_level = {item["level"]: item for item in level_results}
    missing = [level for level in REQUIRED_LEVELS if level not in by_level]
    single_p95 = by_level.get(1, {}).get("p95_latency_seconds", 0.0)
    four_p95 = by_level.get(4, {}).get("p95_latency_seconds", 0.0)
    ratio = (
        four_p95 / single_p95 if not missing and single_p95 > 0 else None
    )
    latency_pass = (
        not missing
        and ratio is not None
        and ratio <= LATENCY_RATIO_THRESHOLD
    )

    peak = max((item["primary_gauge_peak"] for item in level_results), default=0.0)
    gauge_pass = peak > 1.0
    contamination_pass = all(
        item["contamination_check"]["passed"] for item in level_results
    )

    gates = {
        "latency_ratio": {
            "passed": latency_pass,
            "single_page_p95_seconds": single_p95,
            "four_page_p95_seconds": four_p95,
            "ratio": round(ratio, 2) if ratio is not None else None,
            "threshold": LATENCY_RATIO_THRESHOLD,
            "missing_levels": missing,
        },
        "vlm_running_gauge": {
            "passed": gauge_pass,
            "peak": peak,
            "threshold": 1.0,
        },
        "cross_page_contamination": {
            "passed": contamination_pass,
            "note": "weak check: distinct real pages must give distinct bodies",
        },
    }
    overall_pass = latency_pass and gauge_pass and contamination_pass

    result = {
        "target_url": args.url,
        "replicas": args.replicas,
        "levels": levels,
        "requests_per_level": args.requests_per_level,
        "metrics_url": args.metrics_url,
        "level_results": level_results,
        "gates": gates,
        "pass": overall_pass,
    }
    print(json.dumps(result, indent=2))

    print(f"Load test (replicas={args.replicas}) levels={levels}", file=sys.stderr)
    for item in level_results:
        print(
            f"  level {item['level']}: {item['success_count']}/{item['requests_sent']} "
            f"ok, p95={item['p95_latency_seconds']}s, "
            f"gauge peak={item['primary_gauge_peak']:g} "
            f"({item['primary_gauge'] or 'no gauge'}), "
            f"contamination={'ok' if item['contamination_check']['passed'] else 'FAIL'}",
            file=sys.stderr,
        )
    ratio_text = f"{ratio:.2f}x" if ratio is not None else "n/a"
    print(
        f"Gates: latency ratio {ratio_text} <= {LATENCY_RATIO_THRESHOLD}x "
        f"({'PASS' if latency_pass else 'FAIL'}); gauge peak {peak:g} > 1 "
        f"({'PASS' if gauge_pass else 'FAIL'}); contamination "
        f"({'PASS' if contamination_pass else 'FAIL'}).",
        file=sys.stderr,
    )
    print(f"RESULT: {'PASS' if overall_pass else 'FAIL'}", file=sys.stderr)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
