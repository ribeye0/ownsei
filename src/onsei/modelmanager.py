"""Global model inventory and durable, isolated preparation jobs."""

import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from filelock import FileLock, Timeout
from onsei import models
from onsei.user_paths import locks_dir, models_dir
from onsei.environment import llama_binary
from onsei.processes import alive, process_identity, process_owned, spawn_options, signal_group
from onsei.project_paths import project_location
from onsei.storage import ConflictError, Store
from onsei.util import ROOT, STATE, atomic_json, gpu_lock_path

BASIC = [models.DEFAULT_ASR, "firered-vad", models.DEFAULT_TRANSLATION]
INFO = models.MODEL_INFO
ACTIVE = {"queued", "running"}


def paths(name):
    if name not in models.REGISTRY:
        raise ValueError("Unknown model")
    directory = STATE / "model-jobs"
    directory.mkdir(parents=True, exist_ok=True)
    return (directory, directory / (name + ".json"))


def read_job(name):
    _, path = paths(name)
    with FileLock(STATE / "model-submit.lock"):
        try:
            state = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        if (path.parent / (state["id"] + ".cancel")).exists():
            state.update(status="cancelled", message="已取消；保留可續傳的檔案")
            return state
        if state["status"] in ACTIVE and (not alive(state)):
            if state.get("pid") or time.time() - state["started_at"] > 20:
                state.update(
                    status="interrupted",
                    error="模型準備程序已中斷；重試會沿用可續傳的檔案",
                    updated_at=time.time(),
                )
                atomic_json(path, state)
        return state


def bytes_on_disk(path):
    return (
        sum((p.stat().st_size for p in path.rglob("*") if p.is_file() and (not p.is_symlink())))
        if path.exists()
        else 0
    )


def installed(name):
    return models.check_manifest(name, verify=False)


def runtime_ready(name):
    kind = models.REGISTRY[name][2]
    if kind == "speech":
        from onsei.backend import binary

        try:
            return binary("speech").is_file()
        except ValueError:
            return False
    if kind == "vad":
        return models.vad_runtime_ready()
    return llama_binary() is not None


def catalog():
    result = []
    roles = {"asr": 0, "translation": 1, "names": 2, "vad": 3}
    order = sorted(models.REGISTRY, key=lambda name: (name not in BASIC, roles[INFO[name][1]]))
    for name in order:
        repo, revision, kind = models.REGISTRY[name]
        label, role, parameters, format_, size, vram = INFO[name]
        have = installed(name)
        runtime_ok = runtime_ready(name)
        job = read_job(name)
        location = models.model_dir(name)
        result.append(
            {
                "id": name,
                "name": label,
                "role": role,
                "parameters": parameters,
                "format": format_,
                "basic": name in BASIC,
                "repo": repo,
                "revision": revision,
                "url": (
                    f"https://github.com/{repo}/tree/{revision}/convert/out"
                    if kind == "vad"
                    else f"https://huggingface.co/{repo}/tree/{revision}"
                ),
                "license_url": (
                    "https://github.com/FireRedTeam/FireRedVAD/blob/main/LICENSE"
                    if kind == "vad"
                    else f"https://huggingface.co/{repo}/blob/{revision}/README.md"
                ),
                "download_bytes_estimate": int(size * 10**9),
                "vram_estimate": vram,
                "installed": have,
                "runtime_ready": runtime_ok,
                "ready": have
                and runtime_ok
                and not (job and (job["status"] in ACTIVE or job["status"] == "failed")),
                "dependencies": [],
                "path": str(location),
                "bytes": bytes_on_disk(location),
                "source_bytes": bytes_on_disk(models_dir() / ".sources" / name),
                "job": job,
            }
        )
    return {"models": result, "basic": BASIC}


def start_download(names):
    if not names or len(names) > len(models.REGISTRY) or any((n not in models.REGISTRY for n in names)):
        raise ValueError("請選擇已登錄的模型")
    requested = list(dict.fromkeys(names))
    # Check the combined selection before creating any jobs or child processes.
    models.check_prepare_space(requested)
    STATE.mkdir(parents=True, exist_ok=True)
    states = []
    with FileLock(STATE / "model-submit.lock"):
        for name in requested:
            directory, path = paths(name)
            old = json.loads(path.read_text()) if path.exists() else None
            if (
                old
                and old["status"] in ACTIVE
                and (alive(old) or (not old.get("pid") and time.time() - old["started_at"] < 20))
            ):
                states.append(old)
                continue
            import uuid

            identity = uuid.uuid4().hex
            state = {
                "id": identity,
                "model": name,
                "status": "queued",
                "stage": "waiting",
                "started_at": time.time(),
                "updated_at": time.time(),
                "pid": None,
                "bytes_done": 0,
                "bytes_total": 0,
                "transfer_bytes_done": 0,
                "files_done": 0,
                "files_total": 0,
                "message": "等待模型準備",
                "error": None,
            }
            atomic_json(path, state)
            env = dict(
                os.environ,
                ONSEI_ROOT=str(ROOT),
                PYTHONPATH=str(ROOT / "src"),
                HF_XET_CACHE=str(models_dir() / ".cache/xet"),
            )
            try:
                with (directory / (identity + ".log")).open("w") as log:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "onsei.modelmanager", "worker", name, identity],
                        cwd=ROOT,
                        env=env,
                        stdout=log,
                        stderr=log,
                        **spawn_options(),
                    )
                threading.Thread(target=process.wait, daemon=True).start()
            except OSError as error:
                state.update(status="failed", error=str(error))
                atomic_json(path, state)
            states.append(state)
    return states


def job_log(name):
    file = log_path(name)
    if file is None:
        return ""
    with file.open("rb") as stream:
        stream.seek(max(0, file.stat().st_size - 200000))
        text = stream.read().decode("utf-8", errors="replace")
        return re.sub("\\x1b\\[[0-?]*[ -/]*[@-~]", "", text).replace("\r", "\n")


def log_path(name):
    state = read_job(name)
    if not state:
        return None
    if not re.fullmatch("[a-f0-9]{32}", state["id"]):
        raise ValueError("Invalid model job ID")
    file = paths(name)[0] / (state["id"] + ".log")
    if not file.exists():
        return None
    return file


def cancel_download(name):
    with FileLock(STATE / "model-submit.lock"):
        path = paths(name)[1]
        state = json.loads(path.read_text())
        if state["status"] not in ACTIVE:
            return state
        if state.get("pid") and alive(state):
            if not process_owned(state, ["onsei.modelmanager", "worker", name, state["id"]], ROOT):
                raise ConflictError("無法確認下載程序身分")
            (path.parent / (state["id"] + ".cancel")).touch()
            signal_group(state["pid"], signal.SIGTERM)
        elif not state.get("pid"):
            (path.parent / (state["id"] + ".cancel")).touch()
        state.update(status="cancelled", message="已取消；保留可續傳的檔案", updated_at=time.time())
        atomic_json(path, state)
        return state


def tasks_pending():
    index = ROOT / "workspace/index.db"
    if not index.exists():
        return False
    with closing(sqlite3.connect(index)) as db:
        for identity, saved in db.execute("SELECT id,path FROM projects"):
            try:
                path = project_location(index.parent, identity, saved)
            except ValueError:
                continue
            if (path / "project.db").exists() and Store(path).active_jobs():
                return True
    return False


def delete_model(name):
    paths(name)
    try:
        with (
            FileLock(locks_dir() / "model-operation.lock", timeout=0),
            FileLock(STATE / "model-submit.lock"),
            FileLock(gpu_lock_path(locks_dir()), timeout=0),
        ):
            job = json.loads(paths(name)[1].read_text()) if paths(name)[1].exists() else None
            if (
                job
                and job["status"] in ACTIVE
                and (alive(job) or (not job.get("pid") and time.time() - job["started_at"] < 20))
                or tasks_pending()
            ):
                raise ConflictError("已有待處理或執行中任務，請完成後再刪除模型")
            removed = 0
            targets = (models.model_dir(name), models_dir() / ".sources" / name)
            for path in targets:
                if path.is_symlink():
                    raise ValueError("模型目錄是符號連結，拒絕刪除")
            for path in targets:
                if path.exists():
                    removed += bytes_on_disk(path)
                    shutil.rmtree(path)
            if job:
                job.update(
                    status="deleted",
                    stage="deleted",
                    message="模型已刪除，可重新下載",
                    error=None,
                    updated_at=time.time(),
                )
                atomic_json(paths(name)[1], job)
            return {"model": name, "removed_bytes": removed}
    except Timeout as error:
        raise ConflictError("模型正在下載或供 GPU 任務使用，請完成後再刪除") from error


def worker(name, identity):
    import traceback

    path = paths(name)[1]
    guard = threading.RLock()
    with FileLock(STATE / "model-submit.lock"):
        state = json.loads(path.read_text())
        if state["id"] != identity or state["status"] != "queued":
            return
        state.update(status="running", pid=os.getpid(), process_identity=process_identity(os.getpid()))
        atomic_json(path, state)
    last_write = 0.0
    print(
        json.dumps(
            {"model": name, "revision": models.REGISTRY[name][1], "job": identity}, ensure_ascii=False
        ),
        flush=True,
    )

    def progress(update):
        nonlocal last_write
        with guard:
            if (path.parent / (identity + ".cancel")).exists():
                return
            changed = state["stage"] != update.get("stage", state["stage"])
            if changed:
                print(json.dumps(update, ensure_ascii=False), flush=True)
                state.update(bytes_done=0, bytes_total=0, transfer_bytes_done=0, files_done=0, files_total=0)
            state.update(update, updated_at=time.time())
            if changed or time.monotonic() - last_write > 0.2:
                # Readers and cancellation use this same lock. Windows can
                # reject an open racing with the atomic replacement.
                with FileLock(STATE / "model-submit.lock"):
                    current = json.loads(path.read_text())
                    if current["id"] == identity and current["status"] == "running":
                        atomic_json(path, state)
                        last_write = time.monotonic()

    try:
        models.prepare(name, progress)
        if not installed(name):
            raise ValueError("模型檔案不完整，請重試或查看詳細紀錄")
        state.update(status="completed", stage="completed", message="模型準備完成")
    except Exception as error:
        traceback.print_exc()
        state.update(status="failed", error=models.preparation_error(error), message="模型準備失敗")
    finally:
        with guard, FileLock(STATE / "model-submit.lock"):
            current = json.loads(path.read_text())
            if (
                current["id"] == identity
                and current["status"] != "cancelled"
                and (not (path.parent / (identity + ".cancel")).exists())
            ):
                state["updated_at"] = time.time()
                atomic_json(path, state)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "worker":
        raise SystemExit("Use Ownsei model management to launch preparation")
    worker(sys.argv[2], sys.argv[3])
