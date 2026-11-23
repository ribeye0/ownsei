"""Read-only checks for Linux and Windows x64 distributions."""

import json
import platform
import shutil
import subprocess
from onsei import backend
from onsei.util import ROOT, STATE

MIN_GLIBC = (2, 38)


def version_tuple(value):
    try:
        return tuple(int(p) for p in value.split("."))
    except (ValueError, AttributeError):
        return ()


def llama_binary():
    try:
        return backend.binary("llama")
    except ValueError:
        return None


def llama_env(binary):
    return backend.environment("llama")


def command(args, *, env=None, timeout=15):
    try:
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)


def inspect_environment(*, runtimes=False, engines=True):
    checks = []

    def add(name, ok, detail, required=True):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "required": required})

    add(
        "platform",
        platform.system() in ("Linux", "Windows") and platform.machine().lower() in ("x86_64", "amd64"),
        f"{platform.system()} {platform.machine()}",
    )
    if platform.system() == "Linux":
        libc, version = platform.libc_ver()
        add(
            "glibc",
            libc == "glibc" and version_tuple(version) >= MIN_GLIBC,
            f"{libc} {version}; baseline >= 2.38",
        )
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        ok, detail = command([path, "-version"]) if path else (False, "Not installed")
        add(name, ok, detail.splitlines()[0] if detail else "")
    inventory = backend.inventory() if engines else {}
    if engines:
        for engine in ("speech", "llama"):
            value = inventory[engine]
            add(
                engine,
                value["error"] is None,
                value["error"] or "; ".join(d["id"] + " " + d["name"] for d in value["devices"]),
            )
        add(
            "vulkan_gpu",
            any(d["id"].startswith("Vulkan") for engine in inventory.values() for d in engine["devices"]),
            "Haas uses Vulkan; translation uses CUDA or Vulkan. CPU execution is also available",
            required=False,
        )
        add("frontend", (ROOT / "frontend/dist/index.html").is_file(), str(ROOT / "frontend/dist"))
    if runtimes:
        for kind in ("vad", "whisper"):
            from onsei.models import venv_python

            python = venv_python(STATE / kind)
            if not python.exists():
                add(kind, False, "Optional runtime not installed", required=False)
                continue
            from onsei.models import worker_env

            script = "import torch,fireredvad" if kind == "vad" else "import ctranslate2,faster_whisper"
            ok, detail = command([str(python), "-c", script], env=worker_env(kind), timeout=30)
            add(kind, ok, detail, required=False)
    usage = shutil.disk_usage(ROOT)
    add(
        "disk",
        usage.free >= 15 * 1024**3,
        f"{usage.free / 1024**3:.1f} GiB available; optional models need additional space",
        required=False,
    )
    return {
        "ready": all(c["ok"] for c in checks if c["required"]),
        "checks": checks,
        "gpus": inventory.get("llama", {}).get("devices", []),
        "devices": inventory,
        "root": str(ROOT),
        "free_bytes": usage.free,
    }


if __name__ == "__main__":
    print(json.dumps(inspect_environment(), ensure_ascii=False, indent=2))
