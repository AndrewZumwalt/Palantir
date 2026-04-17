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

# 2. Python deps
echo "[2/5] Installing Python deps (this may take a minute on first run)..."
"$VENV/bin/pip" install --quiet -e ".[dev]"

# 3. Frontend build.  The web service serves frontend/dist at '/', so a stale
#    bundle will show a broken UI.  Rebuild if node is available; skip with a
#    warning if not (dist must have been committed).
echo "[3/5] Building frontend..."
NODE_BIN=""
if command -v node >/dev/null 2>&1; then
    NODE_BIN="$(dirname "$(command -v node)")"
elif [ -x "$PROJECT_ROOT/.tools/node/bin/node" ]; then
    NODE_BIN="$PROJECT_ROOT/.tools/node/bin"
fi

if [ -n "$NODE_BIN" ]; then
    (
        cd "$PROJECT_ROOT/frontend"
        if [ ! -d node_modules ]; then
            echo "  Installing npm deps (first run)..."
            PATH="$NODE_BIN:$PATH" npm install --silent
        fi
        PATH="$NODE_BIN:$PATH" npm run build --silent
    )
    echo "  Frontend bundle: $PROJECT_ROOT/frontend/dist"
else
    echo "  (no node found; serving whatever is already in frontend/dist)"
fi

# 4. data dirs
mkdir -p "$DATA_DIR/enrollments" "$DATA_DIR/models" "$DATA_DIR/backups" "$DATA_DIR/tls"

# 5. dev env
AUTH_TOKEN="${PALINTIR_AUTH_TOKEN:-devtoken}"
export PALINTIR_ENV="development"
export PALINTIR_REDIS_FAKE="1"
export PALINTIR_DB_PATH="$DATA_DIR/palintir.db"
export PALINTIR_ENROLLMENT_PATH="$DATA_DIR/enrollments"
export PALINTIR_AUTH_TOKEN="$AUTH_TOKEN"

echo "[4/5] Dev config:"
echo "  DB:         $PALINTIR_DB_PATH"
echo "  Redis:      in-process fakeredis"
echo "  Auth token: $AUTH_TOKEN"

echo "[5/5] Starting web service on http://127.0.0.1:8080"
echo "  UI:      open http://127.0.0.1:8080"
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
