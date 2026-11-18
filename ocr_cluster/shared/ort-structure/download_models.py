#!/usr/bin/env python3
"""Build-time model downloader for the ort-structure image.

Reads MODELS_PINNED.md5 (one line per model: "<md5>  <filename>  <pinned HF
URL>"), downloads each URL into /opt/ort/models/ with curl, and verifies the
md5 against the manifest. Any download failure or md5 mismatch exits nonzero,
which fails the Docker build. No model is ever used if its checksum does not
match the pin, so a re-download cannot silently swap a model.

The URLs are pinned to HuggingFace commit hashes (not `main`), so the bytes
are stable and reproducible. The manifest is the single source of truth for
both the commit pins and the checksums.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

MANIFEST = os.environ.get("MODELS_MANIFEST", "/opt/ort/MODELS_PINNED.md5")
DEST_DIR = os.environ.get("MODELS_DEST_DIR", "/opt/ort/models")


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not os.path.exists(MANIFEST):
        print(f"manifest not found: {MANIFEST}", file=sys.stderr)
        return 1
    os.makedirs(DEST_DIR, exist_ok=True)

    ok = 0
    for lineno, raw in enumerate(open(MANIFEST), start=1):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            print(
                f"manifest line {lineno} malformed (want '<md5> <name> <url>'): {line!r}",
                file=sys.stderr,
            )
            return 1
        expected, name, url = parts
        dest = os.path.join(DEST_DIR, name)
        print(f"  fetching {name} <- {url}")
        proc = subprocess.run(
            ["curl", "-sSfL", "--retry", "3", "--retry-delay", "5", "-o", dest, url]
        )
        if proc.returncode != 0:
            print(
                f"  DOWNLOAD FAILED: {name} ({url}): curl exit {proc.returncode}",
                file=sys.stderr,
            )
            return 1
        actual = md5_file(dest)
        if actual != expected:
            print(
                f"  MD5 MISMATCH: {name}: expected {expected}, got {actual}",
                file=sys.stderr,
            )
            return 1
        print(f"    ok ({name}, md5 verified, {os.path.getsize(dest)} bytes)")
        ok += 1

    if ok == 0:
        print(f"no models found in {MANIFEST}", file=sys.stderr)
        return 1
    print(f"  all {ok} models in {MANIFEST} downloaded and md5-verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
