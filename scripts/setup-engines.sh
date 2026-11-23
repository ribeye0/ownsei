#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .runtime-envs
exec .venv/bin/python scripts/setup_engines.py
