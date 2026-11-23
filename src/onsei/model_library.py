"""Switch or move receipt-backed models without taking ownership of other files."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from filelock import FileLock, Timeout

from onsei import modelmanager as manager, models
from onsei.diskspace import existing_directory, require_space
from onsei.storage import ConflictError
from onsei.user_paths import config_dir, data_dir, download_cache, library_setting, locks_dir, models_dir
from onsei.util import atomic_json, file_hash, gpu_lock_path


def library_info():
    root = models_dir()
    unavailable = library_setting().exists() and not root.is_dir()
    try:
        free = None if unavailable else shutil.disk_usage(existing_directory(root)).free
    except OSError:
        free = None
    return {
        "path": str(root),
        "default_path": str(data_dir() / "models"),
        "cache": str(download_cache(root)),
        "free_bytes": free,
        "legacy": root == config_dir() / "models",
        "unavailable": unavailable,
    }


def open_library():
    root = models_dir()
    if library_setting().exists() and not root.is_dir():
        raise ValueError("模型庫無法使用，請連接磁碟或更換位置")
    root.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(root))
    else:
        subprocess.Popen(
            ["xdg-open", str(root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return {"ok": True}


def _linked(path):
    return path.is_symlink() or path.is_junction()


def _model_files(name, directory):
    receipt = directory / "onsei-model.json"
    if (
        _linked(directory)
        or _linked(receipt)
        or not models.check_manifest(name, verify=False, directory=directory)
    ):
        raise ValueError(f"模型目錄不完整或含連結：{directory}")
    files = list(json.loads(receipt.read_text(encoding="utf-8"))["sha256"])
    if any(_linked(directory / item) for item in files):
        raise ValueError(f"模型檔案含連結：{directory}")
    return [*files, receipt.name]


def _move(source, target):
    if not source.is_dir() and library_setting().exists():
        raise ValueError("來源模型庫無法使用")
    plan = []
    for name in models.REGISTRY:
        origin = source / models.model_dir(name).name
        if not origin.exists() or not (origin / "onsei-model.json").exists():
            continue
        files = _model_files(name, origin)
        dest = target / origin.name
        if (dest / "onsei-model.json").exists():
            if set(_model_files(name, dest)) != set(files) or not models.check_manifest(name, directory=dest):
                raise ValueError(f"目的地已有不同的模型：{dest}")
            if (
                json.loads((origin / files[-1]).read_text())["sha256"]
                != json.loads((dest / files[-1]).read_text())["sha256"]
            ):
                raise ValueError(f"目的地已有不同的模型：{dest}")
        plan.append((name, origin, dest, files, (dest / "onsei-model.json").is_file()))
    same_volume = existing_directory(source).stat().st_dev == target.stat().st_dev
    if not same_volume:
        require_space(
            [
                (
                    target,
                    sum(
                        (origin / f).stat().st_size
                        for _, origin, _, files, present in plan
                        if not present
                        for f in files
                    ),
                )
            ]
        )
    for name, origin, dest, files, present in plan:
        if present:
            continue
        with TemporaryDirectory(dir=target, prefix=".ownsei-move-") as temp:
            staged = Path(temp) / dest.name
            staged.mkdir()
            for filename in files:
                src, out = origin / filename, staged / filename
                if same_volume:
                    try:
                        os.link(src, out)
                        continue
                    except OSError as error:
                        if error.errno not in {
                            errno.EXDEV,
                            errno.EPERM,
                            errno.EACCES,
                            errno.ENOTSUP,
                            errno.ENOSYS,
                        } and getattr(error, "winerror", None) not in {1, 17, 50}:
                            raise
                shutil.copy2(src, out)
            if not models.check_manifest(name, directory=staged):
                raise ValueError(f"搬移校驗失敗：{name}")
            if not dest.exists():
                staged.rename(dest)
            else:
                # A previous move may have left download metadata in the old
                # directory. Publish missing files, leaving other content alone.
                for filename in files:
                    out = dest / filename
                    if _linked(out) or (
                        out.exists() and (not out.is_file() or file_hash(out) != file_hash(staged / filename))
                    ):
                        raise ValueError(f"目的地已有不同的檔案：{out}")
                for filename in files:  # receipt last
                    if not (dest / filename).exists():
                        (staged / filename).rename(dest / filename)
    atomic_json(library_setting(), {"models_dir": str(target)})
    # Only unlink receipt-listed files after the destination is live. Preserve
    # unrelated files and legacy download metadata, including on retry.
    leftovers = []
    for _, origin, _, files, _ in plan:
        try:
            for filename in files:
                (origin / filename).unlink(missing_ok=True)
            if not any(origin.iterdir()):
                origin.rmdir()
        except OSError:
            leftovers.append(str(origin))
    return leftovers


def change_library(path, mode, expected_source=None):
    raw = Path(path).expanduser()
    if not path.strip() or not raw.is_absolute():
        raise ValueError("請輸入模型庫的絕對路徑")
    target = raw.resolve()
    try:
        with (
            FileLock(locks_dir() / "model-operation.lock", timeout=0),
            FileLock(locks_dir() / "library-change.lock", timeout=0),
            FileLock(gpu_lock_path(locks_dir()), timeout=0),
        ):
            source = models_dir().resolve()
            if expected_source is not None and Path(expected_source).resolve() != source:
                raise ConflictError("模型庫已變更，請重新選擇位置")
            if target == source:
                return {**library_info(), "leftovers": []}
            if target.is_relative_to(source) or source.is_relative_to(target):
                raise ValueError("新舊模型庫不可互相包含")
            cache = download_cache(source).resolve()
            if target.is_relative_to(cache) or cache.is_relative_to(target):
                raise ValueError("請使用獨立於下載快取的資料夾")
            if manager.tasks_pending() or any(
                manager.live_download(json.loads(p.read_text()))
                for p in (manager.STATE / "model-jobs").glob("*.json")
            ):
                raise ConflictError("請等待下載與處理任務完成後再更換模型庫")
            target.mkdir(parents=True, exist_ok=True)
            known = [target / models.model_dir(n).name for n in models.REGISTRY]
            recognized = any((p / "onsei-model.json").is_file() for p in known)
            standard = target in {(config_dir() / "models").resolve(), (data_dir() / "models").resolve()}
            if (
                not standard
                and not recognized
                and any(not p.name.startswith(".ownsei-move-") for p in target.iterdir())
            ):
                raise ValueError("請選擇空資料夾或已有 Ownsei 模型的資料夾")
            for name, entry in zip(models.REGISTRY, known):
                if _linked(entry):
                    raise ValueError(f"模型目錄含連結：{entry}")
                if (entry / "onsei-model.json").exists():
                    _model_files(name, entry)
                elif entry.exists() and (
                    not entry.is_dir()
                    or (mode == "select" and any(p.name != ".cache" for p in entry.iterdir()))
                ):
                    raise ValueError(f"目的地模型目錄有其他檔案：{entry}")
            with TemporaryDirectory(dir=target):
                pass  # Verify writability before persisting a new location.
            if mode == "move":
                leftovers = _move(source, target)
            elif mode == "select":
                atomic_json(library_setting(), {"models_dir": str(target)})
                leftovers = []
            else:
                raise ValueError("Unknown library operation")
            return {**library_info(), "leftovers": leftovers}
    except Timeout as error:
        raise ConflictError("模型庫正在使用中，請稍後再試") from error
