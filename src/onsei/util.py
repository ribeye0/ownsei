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
# A Windows release can install FFmpeg locally without changing the user's PATH.
for _directory in (ROOT / "bin/ffmpeg/bin", ROOT / "bin/ffmpeg"):
    if _directory.is_dir():
        os.environ["PATH"] = str(_directory) + os.pathsep + os.environ.get("PATH", "")


def gpu_lock_path(state: Path) -> Path:
    """Resolve an intentionally shared GPU lock before filelock opens it.

    filelock uses O_NOFOLLOW on the final component. Deployments may share this
    lock through a symlink, so all GPU users must open the same real file. Never
    replace/unlink it: that could allow two independent locks on different inodes.
    """
    path = state / "gpu.lock"
    try:
        return path.resolve()
    except (OSError, RuntimeError) as error:
        raise ValueError(f"無法解析 GPU 共用鎖路徑，請檢查符號連結：{path}") from error


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
