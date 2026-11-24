#!/usr/bin/env python3
"""Probe cross-request VLM concurrency through the gateway.

Fires N distinct real page images concurrently at ``POST /layout-parsing`` and
polls SGLang's ``/metrics`` endpoint while the requests are in flight. The
running-requests gauge shows whether the PaddleX serving layer serializes
concurrent page requests or already batches crops across them.

The gauge name is discovered, never assumed: every metric line whose name
contains ``running`` or ``inflight`` is recorded, and the most gauge-like one
is picked. The verdict classifies the observed peak as serialized, parallel,
or borderline.

Set ``GATEWAY_API_KEY`` or pass ``--api-key`` to supply credentials.

Depends only on the stdlib plus ``requests``. A JSON result goes to stdout so
it can be captured; a short human summary goes to stderr. Exit is always 0 for
a completed probe, whatever the verdict.
"""

import argparse
import base64
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

DEFAULT_URL = "http://hybrid-gateway:8080/layout-parsing"
DEFAULT_METRICS_URL = "http://vlm-server:8000/metrics"
DEFAULT_IMAGE_DIR = "worker/tests/integration/test_docs"
DEFAULT_API_KEY = os.environ.get("GATEWAY_API_KEY", "")

# A Prometheus sample: name{labels} value [timestamp].
SAMPLE_RE = re.compile(
    r"^([A-Za-z_:][\w:]*)(?:\{(.*)\})?\s+([-+0-9.eE]+)(?:\s+\d+)?$"
)
LABEL_RE = re.compile(r'(\w+)="([^"]*)"')


def parse_metric_lines(text: str) -> list[tuple[str, str, float, dict]]:
    """Parse Prometheus text into (raw_line, name, value, labels) tuples."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = SAMPLE_RE.match(line)
        if not match:
            continue
        name, labels_raw, value = match.group(1), match.group(2) or "", match.group(3)
        labels = {k: v for k, v in LABEL_RE.findall(labels_raw)}
        out.append((line, name, float(value), labels))
    return out


def is_target_metric(name: str) -> bool:
    low = name.lower()
    return "running" in low or "inflight" in low or "in_flight" in low


def gauge_score(name: str) -> int:
    """Rank a discovered name by how likely it is the running-requests gauge."""
    low = name.lower()
    if low.endswith(("_bucket", "_sum", "_count")):
        return -1000
    score = 0
    if "num_running_reqs" in low or "num_running_requests" in low:
        score += 100
    elif low.endswith(("running_reqs", "running_requests")):
        score += 90
    if "running" in low:
        score += 10
    if "inflight" in low or "in_flight" in low:
        score += 5
    if "unique" in low or "routing_key" in low or "queue" in low:
        score -= 20
    return score


def choose_primary_gauge(names: list[str]) -> str | None:
    if not names:
        return None
    return max(dict.fromkeys(names), key=gauge_score)


def pick_images(image_dir: str, n: int) -> list[Path]:
    exts = (".png", ".jpg", ".jpeg")
    files = sorted(
        (
            p
            for p in Path(image_dir).iterdir()
            if p.is_file() and p.suffix.lower() in exts
        ),
        key=lambda p: p.stat().st_size,
    )
    if len(files) < n:
        raise SystemExit(
            f"need {n} distinct page images in {image_dir!r}, found {len(files)}"
        )
    # Smallest first keeps the probe quick; these are still real page images.
    return files[:n]


def post_page(
    url: str, api_key: str, b64: str, index: int, image: str, timeout: float
) -> dict:
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
        result = {
            "index": index,
            "image": image,
            "status": response.status_code,
            "wall_seconds": round(wall, 2),
        }
        if response.status_code != 200:
            result["body_excerpt"] = response.text[:1000]
        return result
    except Exception as exc:  # noqa: BLE001  # a failed request is a probe result
        return {
            "index": index,
            "image": image,
            "status": None,
            "wall_seconds": round(time.time() - started, 2),
            "error": repr(exc),
        }


def poll_metrics(
    metrics_url: str,
    interval: float,
    stop: threading.Event,
    samples: list,
    discovered: set,
    lock: threading.Lock,
) -> None:
    """Poll /metrics until ``stop`` is set; append raw per-poll samples."""
    while not stop.is_set():
        now = time.time()
        try:
            response = requests.get(metrics_url, timeout=5)
            if response.status_code == 200:
                matched = [
                    item
                    for item in parse_metric_lines(response.text)
                    if is_target_metric(item[1])
                ]
                with lock:
                    for raw_line, _, _, _ in matched:
                        discovered.add(raw_line)
                    samples.append(
                        {
                            "t": round(now, 3),
                            "samples": [
                                {"line": raw, "name": name, "value": value}
                                for raw, name, value, _ in matched
                            ],
                        }
                    )
            else:
                with lock:
                    samples.append(
                        {
                            "t": round(now, 3),
                            "error": f"metrics HTTP {response.status_code}",
                        }
                    )
        except Exception as exc:  # noqa: BLE001  # a bad scrape must not kill the poll
            with lock:
                samples.append({"t": round(now, 3), "error": repr(exc)})
        stop.wait(interval)


def verdict_for(peak: float, median: float) -> tuple[str, str]:
    """Classify the gauge peak as serialized, parallel, or borderline."""
    if peak <= 1.5 or median <= 1.05:
        text = (
            "Gauge near 1: the serving layer serializes cross-request page "
            "work. Concurrent requests are not overlapping at the VLM, so "
            "gateway-side concurrency alone will not raise throughput."
        )
        return ("serialized", text)
    if peak >= 8.0:
        text = (
            "Gauge climbs toward ~16: cross-request page parallelism already "
            "exists in the serving layer. Throughput gains come from bounded "
            "load and page overlap rather than hop removal."
        )
        return ("parallel", text)
    text = (
        f"Gauge peaks at {peak:g} (median {median:g}), between 1 and ~16: the "
        "serving layer partially parallelizes cross-request work."
    )
    return ("borderline", text)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=DEFAULT_URL, help="layout-parsing endpoint")
    ap.add_argument("--api-key", default=DEFAULT_API_KEY)
    ap.add_argument("--n", type=int, default=4, help="concurrent page requests")
    ap.add_argument("--image-dir", default=DEFAULT_IMAGE_DIR)
    ap.add_argument(
        "--image-names",
        default=None,
        help="comma-separated filenames from --image-dir to use instead of the "
        "smallest N (lets a confirmatory run pick content-rich pages)",
    )
    ap.add_argument("--metrics-url", default=DEFAULT_METRICS_URL)
    ap.add_argument("--poll-interval", type=float, default=0.25)
    ap.add_argument("--timeout", type=float, default=540, help="per-request timeout (s)")
    args = ap.parse_args()

    if not args.api_key:
        ap.error("set GATEWAY_API_KEY or pass --api-key")

    if args.n < 1:
        raise SystemExit("--n must be >= 1")

    if args.image_names:
        wanted = [name.strip() for name in args.image_names.split(",") if name.strip()]
        available = {
            path.name: path
            for path in Path(args.image_dir).iterdir()
            if path.is_file()
        }
        unknown = [name for name in wanted if name not in available]
        if unknown:
            raise SystemExit(f"images not found in {args.image_dir!r}: {unknown}")
        if len(set(wanted)) != len(wanted):
            raise SystemExit("--image-names must be distinct filenames")
        images = [available[name] for name in wanted]
        args.n = len(images)
    else:
        images = pick_images(args.image_dir, args.n)
    encoded = [
        (idx, path.name, base64.b64encode(path.read_bytes()).decode("ascii"))
        for idx, path in enumerate(images)
    ]

    samples: list = []
    discovered: set[str] = set()
    lock = threading.Lock()
    stop = threading.Event()
    poller = threading.Thread(
        target=poll_metrics,
        args=(args.metrics_url, args.poll_interval, stop, samples, discovered, lock),
        daemon=True,
    )

    probe_started = time.time()
    poller.start()
    request_results: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=args.n) as pool:
            futures = {
                pool.submit(
                    post_page, args.url, args.api_key, b64, idx, name, args.timeout
                ): idx
                for idx, name, b64 in encoded
            }
            for future in as_completed(futures):
                request_results.append(future.result())
    finally:
        stop.set()
        poller.join(timeout=5)
    elapsed = time.time() - probe_started

    request_results.sort(key=lambda item: item["index"])
    discovered_lines = sorted(discovered)
    discovered_names = sorted(
        {line.split("{")[0].split()[0] for line in discovered_lines}
    )
    primary = choose_primary_gauge(discovered_names)

    timeline: list[list[float]] = []
    if primary:
        for sample in samples:
            for item in sample.get("samples", []):
                if item["name"] == primary:
                    timeline.append([sample["t"], item["value"]])
    peaks = [value for _, value in timeline]
    peak = max(peaks) if peaks else 0.0
    median = _median(peaks)
    verdict_code, verdict_text = verdict_for(peak, median)

    result = {
        "target_url": args.url,
        "concurrency": args.n,
        "images": [name for _, name, _ in encoded],
        "elapsed_seconds": round(elapsed, 2),
        "requests": request_results,
        "metrics_url": args.metrics_url,
        "discovered_metric_names": discovered_names,
        "discovered_metric_lines": discovered_lines,
        "primary_gauge": primary,
        "primary_gauge_peak": peak,
        "primary_gauge_median": round(median, 3),
        "primary_gauge_timeline": timeline,
        "raw_metric_samples": samples,
        "sample_count": len(samples),
        "verdict": verdict_code,
        "verdict_detail": verdict_text,
    }

    print(json.dumps(result, indent=2))

    ok = sum(1 for item in request_results if item["status"] == 200)
    print(
        f"\nGateway concurrency probe: {ok}/{args.n} requests returned 200 in "
        f"{elapsed:.1f}s; {len(samples)} metric samples.",
        file=sys.stderr,
    )
    print(f"Discovered metrics: {', '.join(discovered_names) or '(none)'}", file=sys.stderr)
    print(f"Primary gauge: {primary or '(none)'}, peak={peak:g}, median={median:g}", file=sys.stderr)
    print(f"Verdict [{verdict_code}]: {verdict_text}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
