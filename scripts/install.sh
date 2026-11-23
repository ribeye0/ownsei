#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for program in ffmpeg ffprobe npm; do
  command -v "$program" >/dev/null || { printf 'Missing: %s\n' "$program" >&2; exit 1; }
done
bash scripts/setup.sh
bash scripts/setup-engines.sh
(cd frontend && npm ci && npm run build)
.venv/bin/ownsei doctor --strict
printf 'Ready. Prepare models with ownsei models prepare, then bash scripts/start.sh\n'
