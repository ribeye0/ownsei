"""Durable progress for lightweight local imports; no GPU/model worker involved."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from filelock import FileLock

from onsei.audio import import_audio
from onsei.storage import Store
from onsei.util import atomic_json
from onsei.processes import process_identity, alive

_active: set[Path] = set()
_guard = threading.RLock()


def read_import(store: Store, identity: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise ValueError("Unknown import")
    path = store.path / "imports" / f"{identity}.json"
    with _guard:
        if not path.exists():
            raise ValueError("Unknown import")
        state = json.loads(path.read_text())
        if state["status"] == "running" and path not in _active:
            try:
                running_process = alive(state) and state["pid"] != os.getpid()
            except ProcessLookupError:
                running_process = False
            except PermissionError:
                running_process = True
            if not running_process:
                state.update(
                    status="interrupted",
                    error="服務已重新啟動；已匯入的音軌保留，請重新匯入以繼續。",
                    updated_at=time.time(),
                )
                atomic_json(path, state)
        return state


def latest_import(store: Store) -> dict | None:
    files = list((store.path / "imports").glob("*.json"))
    if not files:
        return None
    return read_import(store, max(files, key=lambda p: p.stat().st_mtime_ns).stem)


def start_import(store: Store, source: Path) -> dict:
    with _guard, FileLock(store.path / ".import-submit.lock"):
        previous = latest_import(store)
        if previous and previous["status"] == "running":
            raise ValueError("專案已有匯入任務正在執行。")
        identity = uuid.uuid4().hex
        path = store.path / "imports" / f"{identity}.json"
        state = {
            "id": identity,
            "status": "running",
            "stage": "waiting",
            "source": str(source),
            "pid": os.getpid(),
            "process_identity": process_identity(os.getpid()),
            "started_at": time.time(),
            "updated_at": time.time(),
            "files_total": 0,
            "files_done": 0,
            "bytes_total": 0,
            "bytes_done": 0,
            "bytes_read": 0,
            "added": 0,
            "skipped": 0,
            "cached": 0,
            "variants_skipped": 0,
            "current_file": "",
            "error": None,
        }
        atomic_json(path, state)
        _active.add(path)
        initial = dict(state)

        def run():
            last_write = 0.0

            def progress(update):
                nonlocal last_write
                old_stage = state["stage"]
                state.update(update, updated_at=time.time())
                if old_stage != state["stage"] or time.monotonic() - last_write >= 0.1:
                    with _guard:
                        atomic_json(path, state)
                    last_write = time.monotonic()

            try:
                import_audio(store, source, progress=progress)
                state.update(status="completed")
            except Exception as exc:
                state.update(status="failed", error=str(exc))
            finally:
                with _guard:
                    state["updated_at"] = time.time()
                    atomic_json(path, state)
                    _active.discard(path)

        threading.Thread(target=run, name=f"import-{identity[:8]}", daemon=True).start()
        return initial
