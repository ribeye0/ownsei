"""Record a successful installation of this release and dependency lock."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile


def signature(root):
    inputs = ["uv.lock", "pyproject.toml"]
    if (root / "release.json").is_file():
        inputs.append("release.json")
    return {
        "location": str(root),
        "python": list(sys.version_info[:2]),
        "inputs": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in inputs},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    marker = root / ".venv/ownsei-install.json"
    expected = signature(root)
    if not args.complete:
        try:
            return int(json.loads(marker.read_text(encoding="utf-8")) != expected)
        except (OSError, ValueError):
            return 1
    with NamedTemporaryFile(dir=marker.parent, mode="w", encoding="utf-8", delete=False) as file:
        json.dump(expected, file)
    os.replace(file.name, marker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
