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
from huggingface_hub import snapshot_download
from onsei.util import ROOT, STATE, atomic_json, file_hash, gpu_lock_path
from onsei.diskspace import GIB, RESERVE, capacity_message, existing_directory, require_space

from onsei.catalog import (
    REGISTRY as REGISTRY,
    SPEECH_FILES as SPEECH_FILES,
    WHISPER_MODELS as WHISPER_MODELS,
    NATIVE_TIMING_MODELS as NATIVE_TIMING_MODELS,
    ASR_MODELS as ASR_MODELS,
    WHISPER_CONVERSIONS as WHISPER_CONVERSIONS,
    WHISPER_CONVERSION_CONFIG as WHISPER_CONVERSION_CONFIG,
    GGUF as GGUF,
    GGUF_SHA256 as GGUF_SHA256,
    DEFAULT_ASR as DEFAULT_ASR,
    DEFAULT_TRANSLATION as DEFAULT_TRANSLATION,
    DEFAULT_NAMES as DEFAULT_NAMES,
    MODEL_INFO as MODEL_INFO,
    TRANSLATION_MODELS as TRANSLATION_MODELS,
)


def dependencies(name):
    return []


def translation_files(name):
    return model_dir(name) / GGUF[name], None


def required_files(name):
    if name == "firered-vad":
        return ["model.pth.tar", "cmvn.ark"]
    if name in SPEECH_FILES:
        return [SPEECH_FILES[name][0]]
    if name in WHISPER_MODELS:
        return ["config.json", "tokenizer.json", "model.bin"]
    return [GGUF[name]] if name in GGUF else ["config.json"]


def model_dir(name: str) -> Path:
    if name not in REGISTRY:
        raise ValueError(f"Unknown model: {name}")
    return ROOT / "models" / name


def venv_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def runtime_lock(kind: str) -> Path:
    suffix = "-windows" if sys.platform == "win32" else ""
    return ROOT / "runtimes" / f"{kind}{suffix}.lock"


def runtime_python(kind: str) -> Path:
    p = venv_python(STATE / kind)
    if not p.exists():
        raise ValueError(f"Runtime {kind} missing; run ownsei models prepare --model ...")
    return p


def runtime_current(kind):
    env = STATE / kind
    try:
        return (
            venv_python(env).exists()
            and not (env / "onsei-runtime-installing.json").exists()
            and json.loads((env / "onsei-runtime.json").read_text())
            == {"lock_sha256": file_hash(runtime_lock(kind)), "python": "3.12"}
        )
    except (OSError, ValueError):
        return False


def runtime_space_requirements(kind):
    if runtime_current(kind):
        return []
    # Include unpacked packages, wheels and build scratch space, not just weights.
    installed = {"vad": 2, "whisper": 2, "whisper-cuda": 5, "whisper-convert": 5}.get(kind, 2) * GIB
    if sys.platform == "win32":
        cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "uv/cache"
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "uv"
    cache = Path(os.environ.get("UV_CACHE_DIR", cache))
    return [(STATE / kind, installed), (cache, GIB)]


def check_prepare_space(names):
    requirements = []
    kinds = set()
    for name in dict.fromkeys(names):
        if not check_manifest(name, verify=False):
            requirements.append((model_dir(name), int(MODEL_INFO[name][4] * 10**9 * 1.15)))
        kind = REGISTRY[name][2]
        if kind and kind != "speech":
            kinds.add(kind)
    for kind in kinds:
        requirements.extend(runtime_space_requirements(kind))
    require_space(requirements)


def preparation_error(error):
    """Keep technical details in the log, but explain out-of-space failures in UI."""
    text = str(error)
    no_space = isinstance(error, OSError) and error.errno == errno.ENOSPC
    no_space |= any(term in text.lower() for term in ("no space left", "os error 28", "enospc"))
    try:
        if no_space:
            return capacity_message(ROOT)
        if isinstance(error, subprocess.CalledProcessError) or "writer channel closed" in text.lower():
            paths = [ROOT, *[p for p, _ in runtime_space_requirements("vad")]]
            for path in paths:
                if shutil.disk_usage(existing_directory(path)).free < RESERVE:
                    return capacity_message(path)
    except OSError:
        # Failure reporting must still work if the filesystem itself is inaccessible.
        if no_space:
            return "磁碟空間不足，請清理模型與套件快取所在磁碟的空間後重試。詳細錯誤見任務紀錄。"
    return text


def setup_runtime(kind: str, progress=None) -> None:
    uv = ROOT / ".tools" / ("uv.exe" if sys.platform == "win32" else "uv")
    if not uv.exists():
        raise ValueError("Run scripts/setup.sh first")
    lock = runtime_lock(kind)
    if not lock.exists():
        raise ValueError(f"Missing pinned runtime lock: {lock}")
    env = STATE / kind
    marker = env / "onsei-runtime.json"
    installing = env / "onsei-runtime-installing.json"
    signature = {"lock_sha256": file_hash(lock), "python": "3.12"}
    if runtime_current(kind):
        return
    if env.is_symlink():
        raise ValueError("共用執行環境版本不同；請先移除新版環境連結，再重新準備模型")
    STATE.mkdir(parents=True, exist_ok=True)
    notify(progress, "runtime_wait", message=f"準備 {kind} 執行環境；等待目前的 GPU 任務完成")
    with FileLock(STATE / "runtime-install.lock"), FileLock(gpu_lock_path(STATE)):
        if runtime_current(kind):
            return
        require_space(runtime_space_requirements(kind))
        notify(progress, "runtime", message=f"安裝 {kind} 執行環境（套件進度見詳細紀錄）")
        if not venv_python(env).exists():
            subprocess.run([str(uv), "venv", "--python", sys.executable, str(env)], check=True)
        atomic_json(installing, signature)
        command = [str(uv), "pip", "sync", "--python", str(venv_python(env)), str(lock)]
        if (ROOT / ".tools/wheels").exists():
            command += ["--find-links", str(ROOT / ".tools/wheels")]
        subprocess.run(command, check=True)
        atomic_json(marker, signature)
        installing.unlink(missing_ok=True)


def notify(progress, stage, **details):
    if progress is not None:
        progress({"stage": stage, **details})


def progress_bar(progress):
    from tqdm.auto import tqdm

    counters = {"bytes_done": 0, "bytes_total": 0, "transfer_bytes_done": 0, "files_done": 0, "files_total": 0}
    guard = threading.RLock()
    last_report = 0.0
    finished = False

    class ReportProgress(tqdm):
        def __init__(self, *args, **kwargs):
            self._transfer = "download" in kwargs.get("desc", "").lower() or "transfer" in kwargs.get("desc", "").lower()
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
    reporter = progress_bar(progress) if progress is not None else None
    kwargs = {"tqdm_class": reporter} if reporter else {}
    try:
        return snapshot_download(
            repo_id=repo,
            revision=revision,
            local_dir=directory,
            allow_patterns=patterns,
            cache_dir=ROOT / ".tools/hf-cache",
            max_workers=4,
            force_download=force,
            **kwargs,
        )
    finally:
        if reporter:
            # Late progress-bar destruction must not revert verification to download.
            reporter.finish()


def check_manifest(name, progress=None, *, verify=True):
    """One receipt contract; full hashes on preparation, metadata only for UI polling."""
    directory = model_dir(name)
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
        if name in WHISPER_CONVERSIONS and metadata.get("conversion") != WHISPER_CONVERSION_CONFIG:
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
            atomic_json(directory / "onsei-model.json", {**metadata, "repo": REGISTRY[name][0], "revision": REGISTRY[name][1]})
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def prepare(name: str, progress=None) -> Path:
    if name not in REGISTRY:
        raise ValueError(f"Unknown model: {name}")
    STATE.mkdir(parents=True, exist_ok=True)
    notify(progress, "waiting", message="等待其他模型準備任務完成")
    with FileLock(STATE / "model-operation.lock"):
        check_prepare_space([name])
        return _prepare(name, progress)


def _prepare(name, progress):
    repo, revision, kind = REGISTRY[name]
    if kind == "speech":
        from onsei.backend import binary

        binary("speech")
    elif kind:
        setup_runtime(kind, progress)
    if name == "firered-vad":
        from onsei.vad_worker import HASHES

        p = model_dir(name)
        if check_manifest(name, progress):
            return p
        download_snapshot(repo, revision, p, ["VAD/model.pth.tar", "VAD/cmvn.ark"], progress)
        notify(progress, "verify", message="校驗模型檔案", files_done=0, files_total=len(HASHES))
        for index, (filename, expected) in enumerate(HASHES.items(), 1):
            source = p / "VAD" / filename
            if file_hash(source) != expected:
                raise ValueError("FireRedVAD SHA256 mismatch")
            source.replace(p / filename)
            notify(progress, "verify", message=f"校驗 {filename}", files_done=index, files_total=len(HASHES))
        atomic_json(p / "onsei-model.json", {"repo": repo, "revision": revision, "sha256": HASHES})
        return p
    if name in SPEECH_FILES:
        p = model_dir(name)
        if check_manifest(name, progress):
            return p
        filename, expected = SPEECH_FILES[name]
        download_snapshot(repo, revision, p, [filename], progress)
        notify(progress, "verify", message=f"校驗 {filename}")
        if file_hash(p / filename) != expected:
            (p / filename).unlink(missing_ok=True)
            raise ValueError("speech.cpp model SHA256 mismatch")
        atomic_json(
            p / "onsei-model.json", {"repo": repo, "revision": revision, "sha256": {filename: expected}}
        )
        return p
    if name in WHISPER_CONVERSIONS:
        return prepare_whisper(name, progress)
    p = model_dir(name)
    if check_manifest(name, progress):
        return p
    patterns = (
        [GGUF[name], "README.md", "LICENSE*", "NOTICE*"]
        if name in GGUF
        else ["*.json", "*.safetensors", "*.bin", "*.txt", "*.model"]
    )
    download_snapshot(repo, revision, p, patterns, progress, force=(p / "onsei-model.json").exists())
    files = [x for x in p.iterdir() if x.is_file() and x.name != "onsei-model.json"]
    notify(progress, "verify", message="校驗模型檔案", files_done=0, files_total=len(files))
    hashes = {}
    for i, path in enumerate(files):
        hashes[path.name] = file_hash(path)
        notify(progress, "verify", message=f"校驗 {path.name}", files_done=i + 1, files_total=len(files))
    if name in GGUF_SHA256 and hashes.get(GGUF[name]) != GGUF_SHA256[name]:
        (p / GGUF[name]).unlink(missing_ok=True)
        raise ValueError("Translation model SHA256 mismatch; retry to download the invalid file again")
    atomic_json(
        p / "onsei-model.json", {"repo": repo, "revision": revision, "file": GGUF.get(name), "sha256": hashes}
    )
    return p


def prepare_whisper(name: str, progress=None) -> Path:
    """Convert pinned upstream weights on CPU; inference only needs the Whisper runtime."""
    repo, revision, _ = REGISTRY[name]
    destination = model_dir(name)
    manifest = destination / "onsei-model.json"
    expected = {"repo": repo, "revision": revision, "conversion": WHISPER_CONVERSION_CONFIG}
    if manifest.exists():
        try:
            saved = json.loads(manifest.read_text())
            hashes = saved.get("sha256", {})
            notify(progress, "verify", message="校驗已轉換的 Whisper 檔案")
            if all((saved.get(k) == v for k, v in expected.items())) and "model.bin" in hashes:
                if all(
                    (
                        Path(f).name == f
                        and (destination / f).is_file()
                        and (file_hash(destination / f) == h)
                        for f, h in hashes.items()
                    )
                ):
                    return destination
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    source = ROOT / "models/.sources" / name
    download_snapshot(repo, revision, source, ["*.json", "*.safetensors", "*.txt"], progress)
    notify(progress, "verify", message="校驗 Whisper 來源權重")
    if file_hash(source / "model.safetensors") != WHISPER_CONVERSIONS[name]:
        raise ValueError("Whisper source SHA256 mismatch")
    setup_runtime("whisper-convert", progress)
    notify(progress, "convert", message="以 CPU 轉換 Whisper 為 CT2 INT8／FP16；此階段不使用 GPU")
    subprocess.run(
        [
            str(runtime_python("whisper-convert")),
            "-m",
            "onsei.convert_whisper",
            str(source),
            str(destination),
        ],
        env=worker_env("whisper-convert"),
        check=True,
    )
    atomic_json(
        manifest,
        {
            **expected,
            "source_sha256": WHISPER_CONVERSIONS[name],
            "compute_type": "int8_float16",
            "sha256": {x.name: file_hash(x) for x in destination.iterdir() if x.is_file()},
        },
    )
    return destination


def worker_env(kind: str) -> dict:
    from onsei.backend import current

    env = dict(os.environ)
    env["ONSEI_WHISPER_DEVICE"] = current()["whisper_device"]
    env.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        PYTHONPATH=str(ROOT / "src"),
        ONSEI_ROOT=str(ROOT),
    )
    if sys.platform == "win32":
        site = STATE / kind / "Lib/site-packages"
        libs = [str(p) for p in (site / "nvidia").glob("*/bin")]
        variable = "PATH"
    else:
        site = next((STATE / kind / "lib").glob("python*/site-packages"))
        libs = [str(p) for p in (site / "nvidia").glob("*/lib")]
        variable = "LD_LIBRARY_PATH"
    env[variable] = os.pathsep.join(libs + [env.get(variable, "")])
    return env
