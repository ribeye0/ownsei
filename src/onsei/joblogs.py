"""Local job diagnostics. Model logs are bounded to each job's recorded byte range."""

from __future__ import annotations

import json
from pathlib import Path

from onsei.storage import Store


def sources(store: Store, job: dict) -> list[dict]:
    result = [{"path": f"logs/{job['id']}.log", "start": 0}]
    result.extend(job.get("log_sources", []))
    safe = []
    for item in result:
        path = (store.path / item["path"]).resolve()
        if (
            not path.is_relative_to(store.path)
            or path.suffix != ".log"
            or not any(path.is_relative_to(store.path / d) for d in ("logs", "cache"))
        ):
            continue
        safe.append({**item, "resolved": path})
    return safe


def stream_log(store: Store, job: dict):
    header = {
        k: job.get(k)
        for k in (
            "id",
            "action",
            "status",
            "message",
            "created_at",
            "started_at",
            "finished_at",
            "options",
            "metrics",
        )
    }
    yield f"=== {job['action']} / {job['status']} / {job['id']} ===\n".encode()
    if job.get("error"):
        yield ("\n=== 完整錯誤 traceback ===\n" + job["error"] + "\n").encode()
    yield ("\n=== 任務資訊 ===\n" + json.dumps(header, ensure_ascii=False, indent=2) + "\n").encode()
    if not job.get("log_sources"):
        yield "\n此任務未記錄子程序日誌來源（舊任務或未啟動模型）；以下提供完整任務 stdout/stderr。\n".encode()
    for item in sources(store, job):
        yield f"\n=== {item['path']} ===\n".encode()
        path: Path = item["resolved"]
        if not path.is_file():
            yield "日誌檔尚未建立或已不存在。\n".encode()
            continue
        with path.open("rb") as file:
            start = max(0, int(item.get("start", 0)))
            end = min(path.stat().st_size, int(item.get("end", path.stat().st_size)))
            file.seek(start)
            remaining = max(0, end - start)
            while remaining:
                chunk = file.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        yield b"\n"


def read_log(store: Store, job: dict) -> str:
    return b"".join(stream_log(store, job)).decode("utf-8", errors="replace")
