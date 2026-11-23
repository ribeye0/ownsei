#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
rm -f .venv/ownsei-install.json
for program in ffmpeg ffprobe curl; do
  command -v "$program" >/dev/null || { printf 'Missing: %s\n' "$program" >&2; exit 1; }
done
bash scripts/setup.sh
bash scripts/setup-engines.sh
if [[ ! -f frontend/dist/index.html ]]; then
  (cd frontend && npm ci && npm run build)
fi
.venv/bin/python -m onsei.cli doctor --strict
.venv/bin/python scripts/install_state.py --complete
printf 'Ownsei ready. Start with bash ownsei.sh; download models in Model management.\n'
