#!/usr/bin/env python3
"""Generate .devcontainer/docker-compose.yml from docker-compose.yml.

The devcontainer only needs the shared infrastructure services plus its own
editor container. Copying those service definitions by hand drifts, so this
script copies them verbatim from the root compose, prefixes their
container_name with yarrow_dev (container names are global across compose
projects), drops the app services, and adds the devcontainer service.

Usage:
  python3 scripts/sync-dev-compose.py          # regenerate the dev compose
  python3 scripts/sync-dev-compose.py --check  # verify in sync; exit 1 if not

Edit docker-compose.yml, then re-run. CI runs --check to catch drift.
"""
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_COMPOSE = ROOT / "docker-compose.yml"
DEV_COMPOSE = ROOT / ".devcontainer" / "docker-compose.yml"

# Shared infra services to copy from the root compose, in output order.
SHARED_SERVICES = ["postgres", "valkey", "rustfs", "rustfs-volumes", "create-bucket"]

# The editor container. Dev-only; not present in the root compose.
DEVCONTAINER_SERVICE = """  devcontainer:
    image: mcr.microsoft.com/devcontainers/python:1-3.11-bookworm
    container_name: yarrow_devcontainer
    user: "vscode"
    working_dir: /workspace
    volumes:
      - ../:/workspace
    command: sleep infinity
    restart: unless-stopped
"""

HEADER = """# Dev services for the Yarrow devcontainer.
# GENERATED FILE - do not edit by hand.
# Regenerate after editing ../docker-compose.yml:
#   python3 scripts/sync-dev-compose.py
#
# Shared infra services are copied verbatim from ../docker-compose.yml with
# their container_name prefixed yarrow_dev (container names are global across
# compose projects, so this keeps the dev project from colliding with the root
# project). App services are dropped; the devcontainer service is added.
name: yarrow_dev
"""


def parse_compose(text):
    """Split a compose file into {service_name: [lines]} and the volumes section.

    Services are the 2-space-indented keys under the top-level 'services:'
    key. Lines are returned verbatim so formatting and comments are preserved.
    """
    services = {}
    volumes = None
    section = None
    current = None
    buf = []

    def flush():
        nonlocal current, buf
        if current is not None:
            services[current] = _strip_trailing_blank(buf)
            current, buf = None, []

    for line in text.split("\n"):
        if line and not line[0].isspace():
            flush()
            section = line.split(":", 1)[0].strip()
            if section == "volumes":
                volumes = [line]
            continue
        if section == "services":
            m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if m:
                flush()
                current, buf = m.group(1), [line]
                continue
        if current is not None:
            buf.append(line)
        elif section == "volumes" and volumes is not None:
            volumes.append(line)
    flush()
    return services, (volumes or [])


def _strip_trailing_blank(lines):
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def prefix_container_names(block_lines):
    return [
        line.replace("container_name: yarrow_", "container_name: yarrow_dev_")
        for line in block_lines
    ]


def generate(root_text):
    services, volumes = parse_compose(root_text)
    missing = [name for name in SHARED_SERVICES if name not in services]
    if missing:
        sys.exit(f"error: {', '.join(missing)} not found in docker-compose.yml services")

    blocks = [DEVCONTAINER_SERVICE.rstrip("\n")]
    for name in SHARED_SERVICES:
        blocks.append("\n".join(prefix_container_names(services[name])))

    parts = [HEADER, "services:", "\n\n".join(blocks), "", "\n".join(_strip_trailing_blank(volumes))]
    return "\n".join(parts) + "\n"


def main():
    check = "--check" in sys.argv[1:]
    generated = generate(ROOT_COMPOSE.read_text())

    if check:
        current = DEV_COMPOSE.read_text()
        if current != generated:
            print("ERROR: .devcontainer/docker-compose.yml is out of sync with docker-compose.yml.")
            print("Regenerate with: python3 scripts/sync-dev-compose.py")
            print()
            for line in difflib.unified_diff(
                current.splitlines(),
                generated.splitlines(),
                fromfile=".devcontainer/docker-compose.yml",
                tofile="generated",
                lineterm="",
            ):
                print(line)
            sys.exit(1)
        print("OK: .devcontainer/docker-compose.yml is in sync.")
        return

    DEV_COMPOSE.write_text(generated)
    print(f"Wrote {DEV_COMPOSE.relative_to(ROOT)} ({len(SHARED_SERVICES)} shared services + devcontainer).")


if __name__ == "__main__":
    main()
