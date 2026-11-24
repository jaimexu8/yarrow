#!/usr/bin/env python3
"""load test: saturate the OCR queue with concurrent large documents.

This is the back-pressure gate (a live verification step, NOT a CI test).
It uploads N large documents concurrently against the running stack, then polls
each to a terminal state, watching the operator metrics endpoint for queue
pressure. The point is to prove the queue absorbs load without an en-masse
failure (a whole job dying because the queue was the cause) and without a
multi-minute gateway timeout cascade; individual page OCR failures are
acceptable and reported.

Run it from the host (or any machine that can reach the backend) once the new
compose (worker + worker-ocr split) is up:

    python3 scripts/load_test_ocr_queue.py \
        --base-url http://localhost:8000 \
        --email e2e-test@example.com --password Zeb-99-e2e-test \
        --n 12 --doc worker/tests/integration/test_docs/sample.pdf \
        --concurrency 6 --timeout 1800

The script needs only `requests`. It cleans up (soft-deletes) the documents it
uploaded at the end unless --keep is given. Exit code 0 = PASS, 1 = FAIL.
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import requests

# Page error codes that, when they hit EVERY page of a job, indicate the queue
# (or the inference service as a whole) is the cause rather than the content.
QUEUE_CAUSE_CODES = {"queue_timeout", "inference_unavailable"}

# A single page taking this long is a multi-minute gateway timeout. The
# deadline chain caps a page at ~540 s (worker-ocr INFERENCE_TIMEOUT_SECONDS);
# a page that approaches or exceeds this is the timeout cascade we want to see.
MULTI_MINUTE_PAGE_SECONDS = 540


def _login(base_url: str, email: str, password: str) -> str:
    r = requests.post(
        f"{base_url}/api/v1/auth/login",
        data={"username": email, "password": password},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _upload(token: str, base_url: str, doc_path: str, filename: str) -> dict:
    with open(doc_path, "rb") as f:
        r = requests.post(
            f"{base_url}/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {token}"},
            files=[("files", (filename, f, "application/octet-stream"))],
            data={"allow_duplicate": "true"},
            timeout=120,
        )
    r.raise_for_status()
    body = r.json()
    # Bulk upload: per-file result in `accepted` / `rejected`.
    for item in body.get("accepted", []):
        return item
    for item in body.get("rejected", []):
        raise RuntimeError(f"upload rejected: {item}")
    raise RuntimeError(f"upload returned no accepted file: {body}")


def _progress(token: str, base_url: str, document_id: str) -> dict:
    r = requests.get(
        f"{base_url}/api/v1/documents/{document_id}/progress",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _metrics(admin_token: str | None, base_url: str) -> dict | None:
    if not admin_token:
        return None
    try:
        r = requests.get(
            f"{base_url}/api/v1/admin/ocr/metrics",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001  # optional metrics poll; a broken endpoint must not kill the run
        return None


def _poll_to_terminal(
    token: str, base_url: str, document_id: str, admin_token: str | None,
    deadline: float, max_depth: list, max_oldest: list,
) -> dict:
    """Poll one document to a terminal job state; track queue pressure."""
    started = time.time()
    page_oldest_seen: dict[str, float] = {}
    while time.time() < deadline:
        p = _progress(token, base_url, document_id)
        job_status = p.get("job_status")
        if job_status in ("completed", "failed", "canceled"):
            break
        m = _metrics(admin_token, base_url)
        if m:
            max_depth[0] = max(max_depth[0], m.get("queue_depth", 0))
            oldest = m.get("oldest_queued_seconds")
            if oldest is not None:
                max_oldest[0] = max(max_oldest[0], oldest)
        # Track per-page wall time (started_at -> now while processing). The
        # progress endpoint returns naive UTC timestamps, so treat a naive
        # value as UTC before converting to a POSIX timestamp.
        for page in p.get("pages", []):
            pn = str(page.get("page_number"))
            if page.get("status") == "processing" and page.get("started_at"):
                try:
                    t0 = datetime.fromisoformat(page["started_at"])
                    if t0.tzinfo is None:
                        t0 = t0.replace(tzinfo=UTC)
                    page_oldest_seen[pn] = max(
                        page_oldest_seen.get(pn, 0.0), time.time() - t0.timestamp()
                    )
                except ValueError:
                    pass
        time.sleep(5)
    return {
        "document_id": document_id,
        "wall_seconds": round(time.time() - started, 1),
        "job_status": p.get("job_status"),
        "document_status": p.get("document_status"),
        "pages_total": p.get("pages_total"),
        "pages_completed": p.get("pages_completed"),
        "pages_failed": p.get("pages_failed"),
        "failed_page_codes": _failed_codes(p),
        "max_page_wall_seconds": round(max(page_oldest_seen.values(), default=0.0), 1),
    }


def _failed_codes(progress: dict) -> dict:
    out = {}
    for page in progress.get("pages", []):
        if page.get("status") == "failed":
            code = page.get("error_code") or "unknown"
            out[str(page.get("page_number"))] = code
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--n", type=int, default=12, help="number of documents to upload")
    ap.add_argument("--doc", required=True, help="path to the document file to re-upload")
    ap.add_argument("--concurrency", type=int, default=6, help="parallel uploads")
    ap.add_argument(
        "--timeout",
        type=int,
        default=2700,
        help="per-doc polling deadline (s). Under a saturated queue the last "
        "doc can take ~40 min for 8 x 28-page docs on 3 workers, so the "
        "default leaves headroom above that.",
    )
    ap.add_argument("--admin-token", default=None, help="admin JWT to poll /admin/ocr/metrics")
    ap.add_argument("--keep", action="store_true", help="do not delete the uploaded docs")
    args = ap.parse_args()

    token = _login(args.base_url, args.email, args.password)
    print(f"logged in as {args.email}")

    # Upload N copies concurrently (same source doc, distinct client ids via
    # allow_duplicate). Track the document id per upload.
    doc_ids: list[str] = []
    upload_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [
            ex.submit(_upload, token, args.base_url, args.doc, f"load_{i:03d}_test")
            for i in range(args.n)
        ]
        for i, fut in enumerate(as_completed(futs)):
            try:
                item = fut.result()
                doc_ids.append(item.get("document_id") or item.get("id"))
            except Exception as e:  # noqa: BLE001
                upload_errors.append(f"upload {i}: {e}")
    if not doc_ids:
        print(f"FAIL: no uploads succeeded. errors={upload_errors}")
        return 1
    if upload_errors:
        print(f"WARNING: {len(upload_errors)} upload(s) failed: {upload_errors}")
    print(f"uploaded {len(doc_ids)} documents; polling to terminal "
          f"(timeout {args.timeout}s each)...")

    deadline = time.time() + args.timeout
    max_depth = [0]
    max_oldest = [0.0]
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {
            ex.submit(
                _poll_to_terminal, token, args.base_url, did, args.admin_token,
                deadline, max_depth, max_oldest,
            ): did
            for did in doc_ids
        }
        for fut in as_completed(futs):
            results.append(fut.result())

    # Verdict
    en_masse = []
    timeout_cascade = []
    individual_fail = []
    for r in results:
        codes = r["failed_page_codes"]
        if r["job_status"] == "failed":
            total = r["pages_total"] or 0
            failed = r["pages_failed"] or 0
            # Every page failed with a queue-cause code -> the queue killed it.
            if total and failed == total and codes and all(
                c in QUEUE_CAUSE_CODES for c in codes.values()
            ):
                en_masse.append(r)
            elif codes:
                individual_fail.append(r)
        if r["max_page_wall_seconds"] >= MULTI_MINUTE_PAGE_SECONDS:
            timeout_cascade.append(r)

    verdict = "PASS"
    if en_masse:
        verdict = "FAIL"
        print(f"\nEN-MASSE FAILURE ({len(en_masse)} job(s) died from the queue):")
        for r in en_masse:
            print(f"  doc {r['document_id']} status={r['job_status']} codes={r['failed_page_codes']}")
    if timeout_cascade:
        print(f"\nNOTE: {len(timeout_cascade)} document(s) had a page near/exceeding "
              f"{MULTI_MINUTE_PAGE_SECONDS}s (gateway timeout cascade):")
        for r in timeout_cascade:
            print(f"  doc {r['document_id']} max_page_wall={r['max_page_wall_seconds']}s "
                  f"job_status={r['job_status']}")
    if individual_fail:
        print(f"\nNOTE: {len(individual_fail)} document(s) had individual page "
              f"failures (acceptable):")
        for r in individual_fail:
            print(f"  doc {r['document_id']} failed={r['failed_page_codes']}")

    completed = sum(1 for r in results if r["job_status"] == "completed")
    print("\n=== SUMMARY ===")
    print(f"verdict: {verdict}")
    print(f"documents: {len(results)} uploaded, {completed} completed, "
          f"{sum(1 for r in results if r['job_status'] == 'failed')} failed, "
          f"{sum(1 for r in results if r['job_status'] not in ('completed', 'failed'))} other")
    print(f"max OCR queue depth observed: {max_depth[0]}")
    print(f"max oldest-queued age observed: {round(max_oldest[0], 1)}s")
    print("per-document:")
    for r in sorted(results, key=lambda x: x["wall_seconds"], reverse=True):
        print(f"  {r['document_id']}  {r['wall_seconds']}s  "
              f"job={r['job_status']} doc={r['document_status']} "
              f"pages {r['pages_completed']}/{r['pages_total']} ok, "
              f"{r['pages_failed']} failed  maxpage={r['max_page_wall_seconds']}s")

    # Cleanup (soft delete) unless --keep.
    if not args.keep and doc_ids:
        print("\ncleaning up (soft-deleting uploaded documents)...")
        for did in doc_ids:
            try:
                requests.delete(
                    f"{args.base_url}/api/v1/documents/{did}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                )
            except Exception as e:  # noqa: BLE001
                print(f"  delete {did} failed: {e}")

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
