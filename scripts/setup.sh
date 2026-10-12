#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .tools/uv ]] || [[ $(.tools/uv --version) != "uv 0.13.0"* ]]; then
  installer=$(mktemp)
  trap 'rm -f "$installer"' EXIT
  curl -LsSf https://astral.sh/uv/0.13.0/install.sh -o "$installer"
  UV_INSTALL_DIR="$PWD/.tools" UV_NO_MODIFY_PATH=1 sh "$installer"
fi
.tools/uv python install 3.12
.tools/uv sync --locked --python 3.12
printf 'Ownsei ready: .venv/bin/ownsei doctor\n'
