"""Device selection shared by isolated jobs, speech.cpp and llama.cpp."""

from __future__ import annotations
import contextlib
import contextvars
import functools
import os
import re
import subprocess
import sys
from pathlib import Path
from onsei.util import ROOT

CURRENT = contextvars.ContextVar("execution", default=None)
DEFAULTS = {"asr_device": "auto", "llm_device": "auto", "vad_engine": "firered", "whisper_device": "cpu"}


def binary(engine):
    if engine not in {"speech", "llama"}:
        raise ValueError(f"Unknown inference engine: {engine}")
    filename = "speech" if engine == "speech" else "llama-server"
    if sys.platform == "win32":
        filename += ".exe"
    override = os.environ.get("ONSEI_SPEECH_BINARY" if engine == "speech" else "ONSEI_LLM_BINARY")
    if override:
        found = Path(override).expanduser().resolve()
        if not found.is_file():
            raise ValueError(f"Inference executable missing: {found}")
        return found
    candidates = sorted((ROOT / "bin" / engine).rglob(filename))
    if not candidates:
        raise ValueError(f"{filename} missing; run bash scripts/setup-engines.sh")
    if len(candidates) != 1:
        raise ValueError(
            f"Multiple {filename} versions installed; select one with ONSEI_LLM_BINARY or ONSEI_SPEECH_BINARY"
        )
    return candidates[0]


def environment(engine):
    path = binary(engine).parent
    windows = sys.platform == "win32"
    libraries = "*.dll" if windows else "*.so*"
    directories = [str(path)] + sorted({str(p.parent) for p in (ROOT / "bin" / engine).rglob(libraries)})
    variable, separator = ("PATH", ";") if windows else ("LD_LIBRARY_PATH", os.pathsep)
    return {
        **os.environ,
        "SPEECH_NO_UPDATE_CHECK": "1",
        variable: separator.join(directories + [os.environ.get(variable, "")]),
    }


@functools.lru_cache(maxsize=2)
def devices(engine):
    command = [str(binary(engine)), "devices" if engine == "speech" else "--list-devices"]
    result = subprocess.run(command, env=environment(engine), capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError(f"{engine} device discovery failed: {result.stderr[-2000:]}")
    rows = []
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        match = re.match(r"\s*((?:Vulkan|CUDA)\d+)(?::\s*|\s+gpu\s+)(.+)", line)
        if match:
            if engine == "llama" or match[1].startswith("Vulkan"):
                rows.append({"id": match[1], "name": match[2]})
    rows.append({"id": "CPU", "name": "CPU"})
    return rows


def normalize_execution(value=None):
    if value is not None and (not isinstance(value, dict) or set(value) - DEFAULTS.keys()):
        raise ValueError("不支援的運算設定")
    defaults = {k: os.environ.get("ONSEI_" + k.upper(), v) for k, v in DEFAULTS.items()}
    result = {**defaults, **(value or {})}
    for key in ("asr_device", "llm_device"):
        v = result[key]
        pattern = r"auto|CPU|Vulkan[0-9]+" if key == "asr_device" else r"auto|CPU|(?:Vulkan|CUDA)[0-9]+"
        if not isinstance(v, str) or not re.fullmatch(pattern, v):
            raise ValueError(f"{key} 裝置格式無效；Haas 支援 Vulkan／CPU，翻譯另支援 CUDA")
    if result["vad_engine"] not in ("firered", "silero", "none"):
        raise ValueError("不支援的 VAD 引擎")
    if result["whisper_device"] not in ("cpu", "cuda"):
        raise ValueError("Whisper 目前僅支援 CPU 或 NVIDIA CUDA")
    return result


def current():
    return normalize_execution(CURRENT.get())


def device(engine):
    requested = current()["asr_device" if engine == "speech" else "llm_device"]
    available = devices(engine)
    preferred = next(
        (d["id"] for d in available if engine == "llama" and d["id"].startswith("CUDA")), available[0]["id"]
    )
    selected = preferred if requested == "auto" else requested
    if selected not in {row["id"] for row in available}:
        raise ValueError(f"{engine} 裝置 {selected} 不存在，請重新選擇運算裝置")
    return selected


@contextlib.contextmanager
def execution_scope(value=None):
    token = CURRENT.set(normalize_execution(value))
    try:
        yield
    finally:
        CURRENT.reset(token)


def inventory():
    result = {}
    for engine in ("speech", "llama"):
        try:
            result[engine] = {"devices": devices(engine), "error": None}
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result[engine] = {"devices": [{"id": "CPU", "name": "CPU"}], "error": str(error)}
    return {**result, "defaults": DEFAULTS}
