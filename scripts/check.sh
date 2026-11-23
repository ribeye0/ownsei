#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/ruff check src scripts tests
.venv/bin/pytest -q
(cd frontend && npm test && npm run build)
.venv/bin/python scripts/package_source.py --check
