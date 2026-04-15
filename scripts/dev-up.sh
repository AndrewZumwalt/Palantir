#!/usr/bin/env bash
# Local development launcher for macOS / Linux.
#
# Sets up a venv, installs the package + dev extras, and starts the web
# service with an in-process fakeredis and user-writable data paths so no
# system dependencies (Redis, sudo) are required.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV="$PROJECT_ROOT/.venv"
DATA_DIR="$PROJECT_ROOT/.dev-data"

echo "=== Palintir dev launcher ==="
echo "Project: $PROJECT_ROOT"

# 1. venv
if [ ! -d "$VENV" ]; then
    echo "[1/4] Creating venv..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade --quiet pip
fi

# 2. deps
echo "[2/4] Installing deps (this may take a minute on first run)..."
"$VENV/bin/pip" install --quiet -e ".[dev]"

# 3. data dirs
mkdir -p "$DATA_DIR/enrollments" "$DATA_DIR/models" "$DATA_DIR/backups" "$DATA_DIR/tls"

# 4. dev env
AUTH_TOKEN="${PALINTIR_AUTH_TOKEN:-devtoken}"
export PALINTIR_ENV="development"
export PALINTIR_REDIS_FAKE="1"
export PALINTIR_DB_PATH="$DATA_DIR/palintir.db"
export PALINTIR_ENROLLMENT_PATH="$DATA_DIR/enrollments"
export PALINTIR_AUTH_TOKEN="$AUTH_TOKEN"

echo "[3/4] Dev config:"
echo "  DB:         $PALINTIR_DB_PATH"
echo "  Redis:      in-process fakeredis"
echo "  Auth token: $AUTH_TOKEN"

echo "[4/4] Starting web service on http://127.0.0.1:8080"
echo "  Health:  curl -H 'Authorization: Bearer $AUTH_TOKEN' http://127.0.0.1:8080/api/health"
echo "  Ctrl-C to stop."
echo ""

exec "$VENV/bin/uvicorn" \
    palintir.web.main:create_app \
    --factory \
    --host 127.0.0.1 \
    --port 8080 \
    --app-dir src \
    --reload
