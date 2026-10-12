"""Read-only deployment checks for the pinned Linux amd64 CUDA distribution."""

import json
import os
import platform
import shutil
import subprocess

from onsei.util import ROOT, STATE

MIN_DRIVER = (570, 124, 6)  # Conservative CUDA 12.8.1 deployment baseline, not the minor-compat minimum.
MIN_GLIBC = (2, 38)  # Required by the pinned Ubuntu llama.cpp shared libraries.


def version_tuple(value):
    try:
        return tuple(int(p) for p in value.split("."))
    except (ValueError, AttributeError):
        return ()


def llama_binary():
    return next((ROOT / ".tools/llama").rglob("llama-server"), None)


def llama_env(binary):
    env = dict(os.environ)
    paths = [str(binary.parent)] + sorted(
        {str(p.parent) for p in (ROOT / ".tools/llama").rglob("libcudart.so*")}
    )
    env["LD_LIBRARY_PATH"] = ":".join(paths + [env.get("LD_LIBRARY_PATH", "")])
    return env


def command(args, *, env=None, timeout=10):
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
        platform.system() == "Linux" and platform.machine() in ("x86_64", "amd64"),
        f"{platform.system()} {platform.machine()}",
    )
    libc, version = platform.libc_ver()
    add(
        "glibc",
        libc == "glibc" and version_tuple(version) >= MIN_GLIBC,
        f"{libc} {version}; baseline >= 2.38",
    )
    for binary in ("ffmpeg", "ffprobe"):
        path = shutil.which(binary)
        ok, detail = command([path, "-version"]) if path else (False, "Not installed")
        add(binary, ok, detail.splitlines()[0] if detail else "")
    smi = shutil.which("nvidia-smi")
    ok, detail = (
        command([smi, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"])
        if smi
        else (False, "Not installed")
    )
    gpu_rows = []
    if ok:
        for row in detail.splitlines():
            pieces = [p.strip() for p in row.split(",")]
            if len(pieces) == 3:
                gpu_rows.append({"name": pieces[0], "driver": pieces[1], "memory_mib": int(pieces[2])})
    add(
        "nvidia",
        bool(gpu_rows) and all(version_tuple(g["driver"]) >= MIN_DRIVER for g in gpu_rows),
        detail + "; Ownsei driver baseline >= 570.124.06",
    )
    if engines:
        binary = llama_binary()
        ok, detail = (
            command([str(binary), "--list-devices"], env=llama_env(binary))
            if binary
            else (False, "Run bash scripts/setup-llama.sh")
        )
        add("llama_cuda", ok and "CUDA0" in detail, detail)
        add(
            "frontend",
            (ROOT / "frontend/dist/index.html").is_file(),
            str(ROOT / "frontend/dist"),
            required=True,
        )
    usage = shutil.disk_usage(ROOT)
    add(
        "disk",
        usage.free >= 15 * 1024**3,
        f"{usage.free / 1024**3:.1f} GiB available; allow more for optional runtimes/models",
        required=False,
    )
    if runtimes:
        from onsei.models import worker_env

        for kind in ("whisper", "cohere", "aligner"):
            binary = STATE / kind / "bin/python"
            if not binary.exists():
                add(kind, False, "Optional runtime not installed", required=False)
                continue
            script = (
                'import ctranslate2; n=ctranslate2.get_cuda_device_count();print("CUDA devices:",n);assert n>0'
                if kind == "whisper"
                else "import torch;print(torch.__version__,torch.version.cuda,torch.cuda.is_available());assert torch.cuda.is_available()"
            )
            ok, detail = command([str(binary), "-c", script], env=worker_env(kind), timeout=30)
            add(kind, ok, detail, required=True)
    return {
        "ready": all(c["ok"] for c in checks if c["required"]),
        "checks": checks,
        "gpus": gpu_rows,
        "root": str(ROOT),
        "free_bytes": usage.free,
    }


if __name__ == "__main__":
    print(json.dumps(inspect_environment(), ensure_ascii=False, indent=2))
