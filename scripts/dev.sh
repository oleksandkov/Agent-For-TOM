#!/usr/bin/env bash
# TOM dev launcher — POSIX (Linux/macOS/Git Bash on Windows).
# Run from anywhere; it cd's into packages/backend, ensures deps are
# installed, and starts the HTTP API on the loopback interface.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "${HERE}/../packages/backend" && pwd)"

cd "${BACKEND}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required (https://docs.astral.sh/uv/)" >&2
  exit 127
fi

uv sync --quiet

HOST="${TOM_HOST:-127.0.0.1}"
PORT="${TOM_PORT:-7878}"

echo "Starting TOM backend on ${HOST}:${PORT} (Ctrl-C to stop)..."
exec uv run python -m backend.tom serve --host "${HOST}" --port "${PORT}"