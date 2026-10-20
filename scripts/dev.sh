#!/usr/bin/env bash
# Development: the agent API on :8000 and the hot-reloading dashboard on :5173.
# The Vite server proxies /api and /ws to the API, so the browser only ever
# talks to one origin. Ctrl-C stops both.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

API_HOST="${JARVIS_API_HOST:-0.0.0.0}"
API_PORT="${JARVIS_API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"

if [ ! -d .venv ]; then
  echo "no .venv found — run: bash scripts/setup.sh" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pids=()
cleanup() {
  echo
  echo "==> stopping"
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> JARVIS API      http://localhost:${API_PORT}   (dashboard: same URL)"
python main.py serve --host "$API_HOST" --port "$API_PORT" &
pids+=($!)

if command -v npm >/dev/null 2>&1; then
  if [ ! -d frontend/node_modules ]; then
    echo "==> installing frontend dependencies"
    ( cd frontend && npm install --no-audit --no-fund )
  fi
  echo "==> JARVIS dashboard http://localhost:${WEB_PORT}  (hot reload, proxies /api + /ws)"
  ( cd frontend && npm run dev -- --host 0.0.0.0 --port "$WEB_PORT" ) &
  pids+=($!)
else
  echo "==> npm not found — serving the built dashboard from :${API_PORT} only"
fi

wait
