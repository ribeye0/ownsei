"""Read-only checks for Linux and Windows x64 distributions."""

import json
import platform
import shutil
import subprocess
import sys
from onsei import backend
from onsei.util import ROOT

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
            any(
                d["id"].startswith("Vulkan")
                for engine in ("speech", "llama")
                for d in inventory[engine]["devices"]
            ),
            "Haas uses Vulkan; translation uses CUDA or Vulkan. CPU execution is also available",
            required=False,
        )
        add("frontend", (ROOT / "frontend/dist/index.html").is_file(), str(ROOT / "frontend/dist"))
    if runtimes:
        from onsei.models import worker_env

        ok, detail = command([sys.executable, "-c", "import ncnn, kaldi_native_fbank"], env=worker_env(), timeout=30)
        add("vad", ok, detail, required=False)
    from onsei.diskspace import existing_directory
    from onsei.user_paths import config_dir, models_dir

    usage = shutil.disk_usage(existing_directory(models_dir()))
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
        "config_dir": str(config_dir()),
        "models_dir": str(models_dir()),
        "free_bytes": usage.free,
    }


if __name__ == "__main__":
    print(json.dumps(inspect_environment(), ensure_ascii=False, indent=2))
