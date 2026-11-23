#!/usr/bin/env bash
set -eu
root=$(cd -- "$(dirname -- "$0")" && pwd -P)
python="$root/.venv/bin/python"
if [[ ! -x "$python" ]]; then python=python3; fi
exec "$python" -B "$root/scripts/uninstall.py" "$@"
