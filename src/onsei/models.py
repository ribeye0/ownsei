from __future__ import annotations

import os
import subprocess
import urllib.request
import hashlib
import json
from pathlib import Path

from filelock import FileLock

from huggingface_hub import snapshot_download

from onsei.util import ROOT, STATE, atomic_json, file_hash

REGISTRY = {
    "qwen3.5-9b-review": (
        "unsloth/Qwen3.5-9B-GGUF",
        "3885219b6810b007914f3a7950a8d1b469d598a5",
        None,
    ),
    "whisper-ja-760m": (
        "efwkjn/faster-whisper-ja-760M",
        "341201ba3e542985d2ad3f78dafb9c1665535013",
        "whisper",
    ),
    "whisper-ja-anime-v0.3": (
        "efwkjn/whisper-ja-anime-v0.3",
        "77edc3f61a06161331b1803252f5987e433dd4a5",
        "whisper",
    ),
    "whisper-ja-1.5b": (
        "efwkjn/whisper-ja-1.5B",
        "72c13fb7b6cc2a831780cdd79cd1a829e77bab02",
        "whisper",
    ),
    "cohere-asr-ja": ("efwkjn/cohere-asr-ja", "8f1794e22b802731bdbf8ce53ff08f96a5af2bb4", "cohere"),
    "aligner": ("Qwen/Qwen3-ForcedAligner-0.6B", "c7cbfc2048c462b0d63a45797104fc9db3ad62b7", "aligner"),
    "hy-mt2-7b": ("tencent/Hy-MT2-7B-GGUF", "ab8472660ac61fac25f1af43fac2599d52a8a775", None),
    "hy-mt2-1.8b": ("tencent/Hy-MT2-1.8B-GGUF", "a0c709d9fac510f2c807aa3af52872340dc37a4a", None),
    "index-translate-9b": (
        "IndexTeam/Index-Translate-9B-GGUF",
        "d01404384ba429b93f5f63d43573812d103b5bcd",
        None,
    ),
    "index-translate-2b": (
        "IndexTeam/Index-Translate-2B-GGUF",
        "449c9e6457b3632d328c6cbb78ae8e8e0c8059a5",
        None,
    ),
    "asmr-qwen3.5-9b": (
        "mmis1000/asmr-qwen3.5-9b-zh-tw-gguf-v0.2",
        "9c40d4deaf8dbfdf32078fe3f85a409e074df72c",
        None,
    ),
}
WHISPER_MODELS = frozenset(name for name, (_, _, kind) in REGISTRY.items() if kind == "whisper")
ASR_MODELS = WHISPER_MODELS | {"cohere-asr-ja"}
# These repositories contain Transformers weights, not ready-to-run CT2 checkpoints.
WHISPER_CONVERSIONS = {
    "whisper-ja-anime-v0.3": "dad598343426a1a687fbfae348c2b22265ad2916a61d31a85ea01e28207a446c",
    "whisper-ja-1.5b": "0bf92cf794ebb2cef2699c24dc71184be6a1ac133cc3c64f228ea08173d0155f",
}
WHISPER_CONVERSION_CONFIG = {
    "pipeline": 1,
    "ctranslate2": "4.8.2",
    "transformers": "4.57.6",
    "quantization": "int8_float16",
}
GGUF = {
    "qwen3.5-9b-review": "Qwen3.5-9B-Q4_K_M.gguf",
    "hy-mt2-7b": "Hy-MT2-7B-Q4_K_M.gguf",
    "hy-mt2-1.8b": "Hy-MT2-1.8B-Q6_K.gguf",
    "index-translate-9b": "Index-Translate-9B.Q4_K_M.gguf",
    "index-translate-2b": "Index-Translate-2B.Q6_K.gguf",
    "asmr-qwen3.5-9b": "asmr-qwen3.5-9b-zh-tw-gguf-v0.2-q4_k_m.gguf",
}
GGUF_SHA256 = {
    "qwen3.5-9b-review": "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8",
    "index-translate-9b": "9cc6758b0007f4cbf42ea3400768cbff91ae74ef8d640e467158348426d33713",
    "index-translate-2b": "3ec6fa4e5a5efa25620f17ecfce8daec1ec8fb35a53c82b419a6707581a505b9",
    "asmr-qwen3.5-9b": "63ca1d7fb97a06106ccc4078c3f473059b5aa76b6283d461c43dfc0cc949d378",
}
DEFAULT_TRANSLATION = "index-translate-9b"
DEFAULT_REVIEW = "index-translate-9b"


def model_dir(name: str) -> Path:
    if name not in REGISTRY:
        raise ValueError(f"Unknown model: {name}")
    return ROOT / "models" / name


def runtime_python(kind: str) -> Path:
    p = STATE / kind / "bin/python"
    if not p.exists():
        raise ValueError(f"Runtime {kind} missing; run ownsei models prepare --model ...")
    return p


def setup_runtime(kind: str, progress=None) -> None:
    uv = ROOT / ".tools/uv"
    if not uv.exists():
        raise ValueError("Run scripts/setup.sh first")
    lock = ROOT / "runtimes" / f"{kind}.lock"
    if not lock.exists():
        raise ValueError(f"Missing pinned runtime lock: {lock}")
    env = STATE / kind
    marker = env / "onsei-runtime.json"
    installing = env / "onsei-runtime-installing.json"
    signature = {"lock_sha256": file_hash(lock), "python": "3.12"}
    try:
        if (
            (env / "bin/python").exists()
            and not installing.exists()
            and json.loads(marker.read_text()) == signature
        ):
            return
    except (OSError, ValueError):
        pass
    STATE.mkdir(parents=True, exist_ok=True)
    notify(progress, "runtime_wait", message=f"準備 {kind} 執行環境；等待目前的 GPU 任務完成")
    with FileLock(STATE / "runtime-install.lock"), FileLock(STATE / "gpu.lock"):
        notify(progress, "runtime", message=f"安裝 {kind} 執行環境（套件進度見詳細紀錄）")
        if not (env / "bin/python").exists():
            subprocess.run([str(uv), "venv", "--python", "3.12", str(env)], check=True)
        atomic_json(installing, signature)
        command = [str(uv), "pip", "sync", "--python", str(env / "bin/python"), str(lock)]
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

    class ReportProgress(tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = False
            super().__init__(*args, **kwargs)

        def refresh(self, *args, **kwargs):
            result = super().refresh(*args, **kwargs)
            if hasattr(self, "n"):
                if self.unit == "B" and not self.desc.startswith("Downloading"):
                    notify(
                        progress,
                        "download",
                        bytes_done=int(self.n),
                        bytes_total=int(self.total or 0),
                        message="下載模型檔案（總量會隨檔案資訊更新）",
                    )
                elif self.unit != "B":
                    notify(progress, "download", files_done=int(self.n), files_total=int(self.total or 0))
            return result

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
    kwargs = {"tqdm_class": progress_bar(progress)} if progress is not None else {}
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


def check_manifest(name, progress=None):
    directory = model_dir(name)
    try:
        metadata = json.loads((directory / "onsei-model.json").read_text())
        if metadata.get("repo") != REGISTRY[name][0] or metadata.get("revision") != REGISTRY[name][1]:
            return False
        if name in WHISPER_CONVERSIONS and metadata.get("conversion") != WHISPER_CONVERSION_CONFIG:
            return False
        hashes = metadata.get("sha256", {})
        if not hashes or (
            name in GGUF and hashes.get(GGUF[name]) != GGUF_SHA256.get(name, hashes.get(GGUF[name]))
        ):
            return False
        required = (
            ["config.json", "tokenizer.json", "model.bin"]
            if name in WHISPER_MODELS
            else ([GGUF[name]] if name in GGUF else ["config.json"])
        )
        if not all((directory / file).is_file() for file in required):
            return False
        notify(progress, "verify", message="校驗已下載檔案", files_done=0, files_total=len(hashes))
        for i, (filename, checksum) in enumerate(hashes.items()):
            path = directory / filename
            if Path(filename).name != filename or not path.is_file() or file_hash(path) != checksum:
                return False
            notify(progress, "verify", message=f"校驗 {filename}", files_done=i + 1, files_total=len(hashes))
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def prepare(name: str, progress=None) -> Path:
    if name not in REGISTRY:
        raise ValueError(f"Unknown model: {name}")
    STATE.mkdir(parents=True, exist_ok=True)
    notify(progress, "waiting", message="等待其他模型準備任務完成")
    with FileLock(STATE / "model-operation.lock"):
        return _prepare(name, progress)


def _prepare(name, progress):
    repo, revision, kind = REGISTRY[name]
    if kind:
        setup_runtime(kind, progress)
    if name in WHISPER_CONVERSIONS:
        return prepare_whisper(name, progress)
    if name == "cohere-asr-ja":
        source = ROOT / ".tools/cohere-source/cohere_asr.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        expected = "c298f88c12138c1ab280ad12891ffb8ce22b4aca4f05bb16a8d006551d42a1ef"
        if not source.exists() or file_hash(source) != expected:
            url = "https://huggingface.co/efwkjn/cohere-asr-ja-lite/raw/4c100172298efe8771f10ecccfee6e47a83e77cb/cohere_asr.py"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Cohere inference source hash mismatch")
            source.write_bytes(data)
    p = model_dir(name)
    if check_manifest(name, progress):
        return p
    patterns = (
        [GGUF[name], "*.json", "README.md"]
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
        p / "onsei-model.json",
        {
            "repo": repo,
            "revision": revision,
            "file": GGUF.get(name),
            "sha256": hashes,
        },
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
            if all(saved.get(k) == v for k, v in expected.items()) and "model.bin" in hashes:
                if all(
                    Path(f).name == f and (destination / f).is_file() and file_hash(destination / f) == h
                    for f, h in hashes.items()
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
    env = dict(os.environ)
    env.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        PYTHONPATH=str(ROOT / "src"),
        ONSEI_ROOT=str(ROOT),
    )
    site = next((STATE / kind / "lib").glob("python*/site-packages"))
    libs = [str(p) for p in (site / "nvidia").glob("*/lib")]
    env["LD_LIBRARY_PATH"] = ":".join(libs + [env.get("LD_LIBRARY_PATH", "")])
    return env
