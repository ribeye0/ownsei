from __future__ import annotations
import os
import sys
import subprocess
import json
import shutil
import errno
import threading
import time
from pathlib import Path
from filelock import FileLock
from onsei.util import ROOT, STATE, atomic_json, file_hash
from onsei.user_paths import download_cache, library_setting, locks_dir, models_dir
from onsei.diskspace import RESERVE, capacity_message, existing_directory, require_space

from onsei.catalog import (
    REGISTRY as REGISTRY,
    FIRERED_BYTES,
    vad_libraries,
    SPEECH_FILES as SPEECH_FILES,
    ASR_MODELS as ASR_MODELS,
    GGUF as GGUF,
    GGUF_SHA256 as GGUF_SHA256,
    DEFAULT_ASR as DEFAULT_ASR,
    DEFAULT_TRANSLATION as DEFAULT_TRANSLATION,
    DEFAULT_NAMES as DEFAULT_NAMES,
    MODEL_INFO as MODEL_INFO,
    TRANSLATION_MODELS as TRANSLATION_MODELS,
)


def translation_files(name):
    return model_dir(name) / GGUF[name], None


def required_files(name):
    if name == "firered-vad":
        from onsei.vad_worker import HASHES

        return list(HASHES)
    if name in SPEECH_FILES:
        return [SPEECH_FILES[name][0]]
    return [GGUF[name]]


def model_dir(name: str) -> Path:
    if name not in REGISTRY:
        raise ValueError(f"Unknown model: {name}")
    return models_dir() / ("firered-vad-ncnn" if name == "firered-vad" else name)


def vad_runtime_ready():
    directory = ROOT / "bin" / "vad"
    platform = "windows" if sys.platform == "win32" else "linux"
    return all((directory / path).is_file() for path in vad_libraries(platform))


def check_prepare_space(names):
    requirements = []
    for name in dict.fromkeys(names):
        if not check_manifest(name, verify=False):
            size = int(MODEL_INFO[name][4] * 10**9 * 1.15)
            destination, cache = model_dir(name), download_cache()
            requirements.append((destination, size))
            if existing_directory(cache).stat().st_dev != existing_directory(destination).stat().st_dev:
                requirements.append((cache, size))
    require_space(requirements)


def preparation_error(error):
    """Keep technical details in the log, but explain out-of-space failures in UI."""
    text = str(error)
    no_space = isinstance(error, OSError) and error.errno == errno.ENOSPC
    no_space |= any(term in text.lower() for term in ("no space left", "os error 28", "enospc"))
    try:
        if no_space:
            return capacity_message(Path(getattr(error, "filename", None) or models_dir()))
        if isinstance(error, subprocess.CalledProcessError) or "writer channel closed" in text.lower():
            path = models_dir()
            if shutil.disk_usage(existing_directory(path)).free < RESERVE:
                return capacity_message(path)
    except OSError:
        # Failure reporting must still work if the filesystem itself is inaccessible.
        if no_space:
            return "磁碟空間不足，請清理模型與套件快取所在磁碟的空間後重試。詳細錯誤見任務紀錄。"
    return text


def setup_runtime(kind: str, progress=None) -> None:
    if kind != "vad":
        raise ValueError("Unknown runtime: " + kind)
    if not vad_runtime_ready():
        notify(progress, "runtime", message="準備 FireRedVAD ncnn CPU 引擎")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/setup_engines.py"), "--engine", "vad"], check=True
        )
    if not vad_runtime_ready():
        raise ValueError("Missing ncnn CPU runtime")


def notify(progress, stage, **details):
    if progress is not None:
        progress({"stage": stage, **details})


def progress_bar(progress):
    from tqdm.auto import tqdm

    counters = {
        "bytes_done": 0,
        "bytes_total": 0,
        "transfer_bytes_done": 0,
        "files_done": 0,
        "files_total": 0,
    }
    guard = threading.RLock()
    last_report = 0.0
    finished = False

    class ReportProgress(tqdm):
        def __init__(self, *args, **kwargs):
            self._transfer = (
                "download" in kwargs.get("desc", "").lower() or "transfer" in kwargs.get("desc", "").lower()
            )
            kwargs["disable"] = False
            super().__init__(*args, **kwargs)

        def _report(self, *, force=False):
            nonlocal last_report
            if not hasattr(self, "n"):
                return
            with guard:
                if finished:
                    return
                done, total = max(0, int(self.n)), max(0, int(self.total or 0))
                if self.unit == "B":
                    if self._transfer:
                        # Xet network traffic can differ from final file size.
                        # Its internal tqdm total is only an estimate, never a percentage.
                        counters["transfer_bytes_done"] = done
                    else:
                        counters.update(bytes_done=done, bytes_total=total)
                else:
                    counters.update(files_done=done, files_total=total)
                now = time.monotonic()
                if force or now - last_report >= 0.2:
                    notify(progress, "download", **counters, message="下載與寫入模型檔案")
                    last_report = now

        def update(self, n=1):
            result = super().update(n)
            # tqdm may suppress refreshes via miniters; updates must still reach UI.
            self._report()
            return result

        def refresh(self, *args, **kwargs):
            result = super().refresh(*args, **kwargs)
            self._report()
            return result

        def close(self):
            super().close()
            self._report(force=True)

        @staticmethod
        def finish():
            nonlocal finished
            with guard:
                if not finished:
                    notify(progress, "download", **counters, message="下載與寫入模型檔案")
                    finished = True

    return ReportProgress


def download_snapshot(repo, revision, directory, patterns, progress, *, force=False):
    notify(
        progress,
        "metadata",
        message="讀取固定版本的模型檔案資訊",
        bytes_done=0,
        bytes_total=0,
        files_done=0,
        files_total=0,
    )
    # The hub reads the Xet cache location when it is first imported.
    os.environ["HF_XET_CACHE"] = str(download_cache() / "xet")
    from huggingface_hub import snapshot_download

    reporter = progress_bar(progress) if progress is not None else None
    kwargs = {"tqdm_class": reporter} if reporter else {}
    try:
        return snapshot_download(
            repo_id=repo,
            revision=revision,
            local_dir=directory,
            allow_patterns=patterns,
            cache_dir=download_cache() / "hub",
            max_workers=4,
            force_download=force,
            **kwargs,
        )
    finally:
        if reporter:
            # Late progress-bar destruction must not revert verification to download.
            reporter.finish()


def check_manifest(name, progress=None, *, verify=True, directory=None):
    """One receipt contract; full hashes on preparation, metadata only for UI polling."""
    directory = model_dir(name) if directory is None else directory
    try:
        metadata = json.loads((directory / "onsei-model.json").read_text())
        if not isinstance(metadata, dict):
            return False
        # The published Q4 is byte-identical to the former local SFT48 release.
        # Accept its pinned receipt, then migrate it after a full checksum pass.
        legacy_sft48 = (
            name == "index-translate-ownsei-sft48-q4-k-m"
            and metadata.get("repo") == "local:Ownsei/Index-Translate-9B-SFT48-GGUF"
            and metadata.get("revision") == GGUF_SHA256[name]
        )
        if not legacy_sft48 and (
            metadata.get("repo") != REGISTRY[name][0] or metadata.get("revision") != REGISTRY[name][1]
        ):
            return False
        hashes = metadata.get("sha256", {})
        if (
            not isinstance(hashes, dict)
            or not hashes
            or (name in GGUF_SHA256 and hashes.get(GGUF[name]) != GGUF_SHA256[name])
        ):
            return False
        required = required_files(name)
        if name in SPEECH_FILES and hashes.get(SPEECH_FILES[name][0]) != SPEECH_FILES[name][1]:
            return False
        if name == "firered-vad":
            from onsei.vad_worker import HASHES

            if any(hashes.get(k) != v for k, v in HASHES.items()):
                return False
        if not all(file in hashes and (directory / file).is_file() for file in required):
            return False
        if any(
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(checksum, str)
            or not (directory / filename).is_file()
            for filename, checksum in hashes.items()
        ):
            return False
        if not verify:
            return True
        notify(progress, "verify", message="校驗已下載檔案", files_done=0, files_total=len(hashes))
        for i, (filename, checksum) in enumerate(hashes.items()):
            path = directory / filename
            if Path(filename).name != filename or not path.is_file() or file_hash(path) != checksum:
                return False
            notify(progress, "verify", message=f"校驗 {filename}", files_done=i + 1, files_total=len(hashes))
        if legacy_sft48:
            atomic_json(
                directory / "onsei-model.json",
                {**metadata, "repo": REGISTRY[name][0], "revision": REGISTRY[name][1]},
            )
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def prepare(name: str, progress=None) -> Path:
    if name not in REGISTRY:
        raise ValueError(f"Unknown model: {name}")
    STATE.mkdir(parents=True, exist_ok=True)
    notify(progress, "waiting", message="等待其他模型準備任務完成")
    with FileLock(locks_dir() / "model-operation.lock"):
        if library_setting().exists() and not models_dir().is_dir():
            raise ValueError("模型庫無法使用，請連接磁碟或更換位置")
        check_prepare_space([name])
        return _prepare(name, progress)


def download_vad_weights(repo, revision, directory, hashes, progress):
    import httpx
    from tempfile import TemporaryDirectory

    directory.mkdir(parents=True, exist_ok=True)
    base = f"https://raw.githubusercontent.com/{repo}/{revision}/convert/out/"
    with TemporaryDirectory(dir=directory) as temp, httpx.Client(follow_redirects=True, timeout=60) as client:
        done = 0
        for index, (name, expected) in enumerate(hashes.items()):
            target = directory / name
            if not target.is_file() or file_hash(target) != expected:
                staged = Path(temp) / name
                with client.stream("GET", base + name) as response, staged.open("wb") as stream:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
                        notify(
                            progress,
                            "download",
                            bytes_done=done + stream.tell(),
                            bytes_total=FIRERED_BYTES,
                            files_done=index,
                            files_total=len(hashes),
                        )
                if file_hash(staged) != expected:
                    raise ValueError("FireRedVAD ncnn SHA256 mismatch: " + name)
                staged.replace(target)
            done += target.stat().st_size
        notify(
            progress,
            "verify",
            message="FireRedVAD ncnn SHA256",
            files_done=len(hashes),
            files_total=len(hashes),
        )


def _prepare(name, progress):
    repo, revision, kind = REGISTRY[name]
    if kind == "speech":
        from onsei.backend import binary

        binary("speech")
    elif kind:
        setup_runtime(kind, progress)
    directory = model_dir(name)
    if check_manifest(name, progress):
        return directory
    staging = download_cache() / "downloads" / name
    if name == "firered-vad":
        from onsei.vad_worker import HASHES

        staging.mkdir(parents=True, exist_ok=True)
        for filename, expected in HASHES.items():
            existing = directory / filename
            if not (staging / filename).exists() and existing.is_file() and file_hash(existing) == expected:
                shutil.copy2(existing, staging / filename)
        download_vad_weights(repo, revision, staging, HASHES, progress)
        hashes = HASHES
    else:
        speech = name in SPEECH_FILES
        filename, expected = SPEECH_FILES[name] if speech else (GGUF[name], GGUF_SHA256.get(name))
        patterns = [filename] if speech else [filename, "README.md", "LICENSE*", "NOTICE*"]
        download_snapshot(
            repo, revision, staging, patterns, progress, force=(directory / "onsei-model.json").exists()
        )
        files = [p for p in staging.iterdir() if p.is_file() and any(p.match(pat) for pat in patterns)]
        hashes = {}
        notify(progress, "verify", message="校驗模型檔案", files_done=0, files_total=len(files))
        for i, path in enumerate(files):
            hashes[path.name] = file_hash(path)
            notify(progress, "verify", message=f"校驗 {path.name}", files_done=i + 1, files_total=len(files))
        if filename not in hashes or (expected and hashes[filename] != expected):
            (staging / filename).unlink(missing_ok=True)
            raise ValueError(
                "speech.cpp model SHA256 mismatch" if speech else "Translation model SHA256 mismatch"
            )
    metadata = {"repo": repo, "revision": revision, "sha256": hashes}
    if name in GGUF:
        metadata["file"] = GGUF[name]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "onsei-model.json").unlink(missing_ok=True)
    for filename, checksum in hashes.items():
        source, target = staging / filename, directory / filename
        try:
            source.replace(target)
        except OSError as error:
            if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
                raise
            from tempfile import TemporaryDirectory

            with TemporaryDirectory(dir=directory) as temp:
                copied = Path(temp) / filename
                shutil.copyfile(source, copied)
                if file_hash(copied) != checksum:
                    raise ValueError("Model SHA256 mismatch")
                copied.replace(target)
            source.unlink()
    atomic_json(directory / "onsei-model.json", metadata)
    return directory


def worker_env() -> dict:
    env = dict(os.environ)
    env.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        PYTHONPATH=os.pathsep.join(map(str, (ROOT / "src", ROOT / "bin/vad"))),
        ONSEI_ROOT=str(ROOT),
    )
    return env
