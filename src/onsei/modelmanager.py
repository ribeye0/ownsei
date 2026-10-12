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
from pathlib import Path

from filelock import FileLock, Timeout

from onsei import models
from onsei.environment import llama_binary
from onsei.processes import alive, process_identity
from onsei.storage import ConflictError, Store
from onsei.util import ROOT, STATE, atomic_json

BASIC = ["whisper-ja-760m", "index-translate-9b"]
INFO = {
    "whisper-ja-760m": ("Whisper JA 760M", "asr", "760M", "CT2 FP16", 1.5, "約 2–4 GiB"),
    "whisper-ja-anime-v0.3": (
        "Whisper JA Anime v0.3",
        "asr",
        "約 760M",
        "CT2 INT8／FP16（首次 CPU 轉換）",
        3.1,
        "約 2–4 GiB",
    ),
    "whisper-ja-1.5b": (
        "Whisper JA 1.5B",
        "asr",
        "1.5B",
        "CT2 INT8／FP16（首次 CPU 轉換）",
        5.0,
        "約 3–5 GiB",
    ),
    "cohere-asr-ja": ("Cohere ASR JA", "asr", "約 2B", "BF16；需要對齊器", 4.2, "約 5–8 GiB"),
    "aligner": ("Qwen3 ForcedAligner", "alignment", "0.6B", "BF16", 1.9, "約 2–4 GiB"),
    "index-translate-9b": ("Index Translate 9B", "translation", "9B", "GGUF Q4_K_M", 5.8, "約 6–9 GiB"),
    "index-translate-2b": ("Index Translate 2B", "translation", "2B", "GGUF Q6_K", 1.6, "約 2–4 GiB"),
    "asmr-qwen3.5-9b": ("ASMR Qwen3.5 9B", "translation", "9B", "GGUF Q4_K_M", 5.8, "約 6–9 GiB"),
    "hy-mt2-7b": ("Hy-MT2 7B", "translation", "7B", "GGUF Q4_K_M", 4.7, "約 5–8 GiB"),
    "hy-mt2-1.8b": ("Hy-MT2 1.8B", "translation", "1.8B", "GGUF Q6_K", 1.5, "約 2–4 GiB"),
    "qwen3.5-9b-review": ("Qwen3.5 9B（實驗性審校）", "review", "9B", "GGUF Q4_K_M", 5.7, "約 6–9 GiB"),
}
ACTIVE = {"queued", "running"}


def paths(name):
    if name not in models.REGISTRY:
        raise ValueError("Unknown model")
    directory = STATE / "model-jobs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory, directory / (name + ".json")


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
        if state["status"] in ACTIVE and not alive(state):
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
        sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())
        if path.exists()
        else 0
    )


def installed(name):
    directory = models.model_dir(name)
    try:
        data = json.loads((directory / "onsei-model.json").read_text())
        if (
            data["repo"] != models.REGISTRY[name][0]
            or data["revision"] != models.REGISTRY[name][1]
            or not data.get("sha256")
        ):
            return False
        if name in models.WHISPER_CONVERSIONS and data.get("conversion") != models.WHISPER_CONVERSION_CONFIG:
            return False
        for filename in data["sha256"]:
            if Path(filename).name != filename or not (directory / filename).is_file():
                return False
        if name in models.WHISPER_MODELS:
            return all((directory / p).is_file() for p in ("model.bin", "config.json", "tokenizer.json"))
        if name in models.GGUF:
            return (directory / models.GGUF[name]).is_file()
        return (directory / "config.json").is_file()
    except (OSError, ValueError, KeyError, TypeError):
        return False


def catalog():
    result = []
    roles = {"asr": 0, "alignment": 1, "translation": 2, "review": 3}
    order = sorted(models.REGISTRY, key=lambda name: (name not in BASIC, roles[INFO[name][1]]))
    for name in order:
        repo, revision, kind = models.REGISTRY[name]
        label, role, parameters, format_, size, vram = INFO[name]
        have = installed(name)
        runtime_ready = (
            (
                (STATE / kind / "bin/python").is_file()
                and not (STATE / kind / "onsei-runtime-installing.json").exists()
            )
            if kind
            else llama_binary() is not None
        )
        if name == "cohere-asr-ja":
            runtime_ready = runtime_ready and (ROOT / ".tools/cohere-source/cohere_asr.py").is_file()
        dependencies = ["aligner"] if name == "cohere-asr-ja" else []
        dependencies_ready = all(
            installed(d)
            and (STATE / models.REGISTRY[d][2] / "bin/python").is_file()
            and not (STATE / models.REGISTRY[d][2] / "onsei-runtime-installing.json").exists()
            for d in dependencies
        )
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
                "url": f"https://huggingface.co/{repo}/tree/{revision}",
                "license_url": f"https://huggingface.co/{repo}/blob/{revision}/README.md",
                "download_bytes_estimate": int(size * 10**9),
                "vram_estimate": vram,
                "installed": have,
                "runtime_ready": runtime_ready,
                "ready": have
                and runtime_ready
                and dependencies_ready
                and not (job and job["status"] == "failed"),
                "dependencies": dependencies,
                "path": str(location),
                "bytes": bytes_on_disk(location),
                "source_bytes": bytes_on_disk(ROOT / "models/.sources" / name),
                "job": job,
            }
        )
    return {"models": result, "basic": BASIC}


def start_download(names):
    if not names or len(names) > len(models.REGISTRY) or any(n not in models.REGISTRY for n in names):
        raise ValueError("請選擇已登錄的模型")
    requested = list(
        dict.fromkeys([d for n in names for d in (["aligner", n] if n == "cohere-asr-ja" else [n])])
    )
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
                HF_XET_CACHE=str(ROOT / ".tools/hf-xet"),
            )
            try:
                with (directory / (identity + ".log")).open("w") as log:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "onsei.modelmanager", "worker", name, identity],
                        cwd=ROOT,
                        env=env,
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                    )
                # Worker owns status writes. A tiny bootstrap grace avoids a PID
                # handoff overwriting a fast worker's completed progress.
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
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text).replace("\r", "\n")


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
            args = Path(f"/proc/{state['pid']}/cmdline").read_bytes().split(b"\0")
            if (
                not all(os.fsencode(v) in args for v in ("onsei.modelmanager", "worker", name, state["id"]))
                or os.getpgid(state["pid"]) != state["pid"]
                or Path(f"/proc/{state['pid']}/cwd").resolve() != ROOT.resolve()
            ):
                raise ConflictError("無法確認下載程序身分")
            (path.parent / (state["id"] + ".cancel")).touch()
            os.killpg(state["pid"], signal.SIGTERM)
        elif not state.get("pid"):
            # The worker checks the saved status before doing any work.
            (path.parent / (state["id"] + ".cancel")).touch()
        state.update(status="cancelled", message="已取消；保留可續傳的檔案", updated_at=time.time())
        atomic_json(path, state)
        return state


def tasks_pending():
    index = ROOT / "workspace/index.db"
    if not index.exists():
        return False
    with sqlite3.connect(index) as db:
        for (path,) in db.execute("SELECT path FROM projects"):
            if (Path(path) / "project.db").exists() and Store(path).active_jobs():
                return True
    return False


def delete_model(name):
    paths(name)  # Whitelist before touching any paths.
    try:
        with (
            FileLock(STATE / "model-operation.lock", timeout=0),
            FileLock(STATE / "model-submit.lock"),
            FileLock(STATE / "gpu.lock", timeout=0),
        ):
            job = json.loads(paths(name)[1].read_text()) if paths(name)[1].exists() else None
            if (
                job
                and job["status"] in ACTIVE
                and (alive(job) or (not job.get("pid") and time.time() - job["started_at"] < 20))
            ) or tasks_pending():
                raise ConflictError("已有待處理或執行中任務，請完成後再刪除模型")
            removed = 0
            targets = (models.model_dir(name), ROOT / "models/.sources" / name)
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
            state.update(update, updated_at=time.time())
            if changed or time.monotonic() - last_write > 0.2:
                atomic_json(path, state)
                last_write = time.monotonic()

    try:
        models.prepare(name, progress)
        if not installed(name):
            raise ValueError("模型檔案不完整，請重試或查看詳細紀錄")
        state.update(status="completed", stage="completed", message="模型準備完成")
    except Exception as error:
        traceback.print_exc()
        state.update(status="failed", error=str(error), message="模型準備失敗")
    finally:
        with guard, FileLock(STATE / "model-submit.lock"):
            current = json.loads(path.read_text())
            if (
                current["id"] == identity
                and current["status"] != "cancelled"
                and not (path.parent / (identity + ".cancel")).exists()
            ):
                state["updated_at"] = time.time()
                atomic_json(path, state)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "worker":
        raise SystemExit("Use Ownsei model management to launch preparation")
    worker(sys.argv[2], sys.argv[3])
