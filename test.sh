#!/usr/bin/env bash
set -euo pipefail

# Point upstream to a closed local port if not set, verifying complete offline execution
export FX_UPSTREAM_BASE="${FX_UPSTREAM_BASE:-http://127.0.0.1:59999}"

pytest test_main.py -v
