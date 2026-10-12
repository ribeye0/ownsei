#!/usr/bin/env bash
# Install the app/engine only. Select model weights later in the web interface.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  printf 'Ownsei requires Linux amd64 (x86_64).\n' >&2; exit 1
fi
for tool in curl tar ffmpeg ffprobe node npm nvidia-smi; do
  if ! command -v "$tool" >/dev/null; then
    printf 'Missing prerequisite: %s. See docs/deployment.md.\n' "$tool" >&2; exit 1
  fi
done
if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)'; then
  printf 'Node.js 22 or later is required.\n' >&2; exit 1
fi
if [[ ${EUID:-$(id -u)} == 0 ]]; then
  printf 'Install and run Ownsei as a regular user, not root.\n' >&2; exit 1
fi
bash scripts/setup.sh
.venv/bin/ownsei doctor --strict --prerequisites
printf '[2/4] Preparing pinned llama.cpp CUDA runtime\n'
bash scripts/setup-llama.sh
printf '[3/4] Building the interface\n'
(cd frontend && npm ci && npm run build)
printf '[4/4] Checking deployment\n'
.venv/bin/ownsei doctor --strict
printf 'Installation ready. Run: bash scripts/start.sh\nThen open http://127.0.0.1:8765 and use Model management.\n'
