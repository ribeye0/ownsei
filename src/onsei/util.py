from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(os.environ.get("ONSEI_ROOT", Path(__file__).resolve().parents[2]))
STATE = ROOT / ".runtime-envs"


def digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_hash(path: Path, progress: Callable[[int], None] | None = None) -> str:
    h = hashlib.sha256()
    done = 0
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
            done += len(block)
            if progress:
                progress(done)
    return h.hexdigest()


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def natural_key(value: str) -> list:
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", value)]


def slug(value: str) -> str:
    return re.sub(r"[^\w\-]+", "_", value).strip("_")[:120] or "track"
