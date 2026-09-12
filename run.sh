#!/usr/bin/env bash
# Sage. one-command start for Linux / Raspberry Pi OS.
# Creates a virtualenv if absent, installs requirements, then serves the app.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    if command -v python3.11 >/dev/null 2>&1; then
        python3.11 -m venv .venv
    else
        python3 -m venv .venv
    fi
fi

if [ ! -x ".venv/bin/uvicorn" ]; then
    .venv/bin/python -m pip install -r requirements.txt
fi

if command -v npm >/dev/null 2>&1 && [ ! -d "node_modules/mermaid" ]; then
    npm ci --ignore-scripts --no-audit --no-fund
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" --reload --reload-dir app --reload-include "*.py"
