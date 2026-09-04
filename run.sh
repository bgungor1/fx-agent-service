#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8080}"
export FX_UPSTREAM_BASE="${FX_UPSTREAM_BASE:-https://api.frankfurter.dev/v1}"

exec uvicorn main:app --host 0.0.0.0 --port "$PORT"