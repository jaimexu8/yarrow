#!/usr/bin/env bash
# Verifies the devcontainer environment against the project's CI commands.
# Run from inside the dev container: bash .devcontainer/verify.sh
set -uo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.poetry-venv/bin:$HOME/.local/bin:$PATH"
export NVM_DIR=/usr/local/share/nvm
if [ -s "$NVM_DIR/nvm.sh" ]; then
  . "$NVM_DIR/nvm.sh"
fi

pass=0
fail=0

check() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS  $name"
    pass=$((pass + 1))
  else
    echo "FAIL  $name"
    fail=$((fail + 1))
  fi
}

# 1. Services reachable over the compose network
if poetry -C backend run python - <<'PY' >/dev/null 2>&1
import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect("postgresql://yarrow:yarrow_password@postgres:5432/yarrow", timeout=5)
    assert await conn.fetchval("SELECT 1") == 1
    await conn.close()

asyncio.run(main())
PY
then echo "PASS  postgres reachable"; pass=$((pass + 1)); else echo "FAIL  postgres reachable"; fail=$((fail + 1)); fi

if poetry -C backend run python - <<'PY' >/dev/null 2>&1
import redis
r = redis.Redis(host="valkey", port=6379, socket_timeout=5)
assert r.ping()
PY
then echo "PASS  valkey reachable"; pass=$((pass + 1)); else echo "FAIL  valkey reachable"; fail=$((fail + 1)); fi

if curl -sf --max-time 5 http://rustfs:9000/health >/dev/null 2>&1; then
  echo "PASS  rustfs reachable"
  pass=$((pass + 1))
else
  echo "FAIL  rustfs reachable"
  fail=$((fail + 1))
fi

if curl -sf --max-time 5 http://rustfs:9000/yarrow-documents >/dev/null 2>&1; then
  echo "PASS  yarrow-documents bucket public (list)"
  pass=$((pass + 1))
else
  echo "FAIL  yarrow-documents bucket public (list)"
  fail=$((fail + 1))
fi

# 2. Python apps import
check "backend app imports" bash -c "cd backend && poetry run python -c 'from app.main import app'"
check "worker celery app imports" bash -c "cd worker && poetry run python -c 'from app.celery_app import celery_app'"

# 3. Tooling
check "poetry available" poetry --version
check "node available" node --version
check "npm available" npm --version
check "ruff (backend)" bash -c "cd backend && poetry run ruff --version"

# 4. CI parity commands
check "backend: ruff check" bash -c "cd backend && poetry run ruff check ."
check "backend: pytest" bash -c "cd backend && poetry run pytest"
check "frontend: tsc --noEmit" bash -c "cd frontend && npx tsc --noEmit"
check "frontend: npm run build" bash -c "cd frontend && npm run build"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
