"""Build platform release bundles with prebuilt UI and hash-pinned native engines."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile

from package_source import source_files, audit
from release_assets import engine_assets
from setup_engines import install_engine

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("linux-x64-vulkan", "linux-x64-cuda", "windows-x64-vulkan", "windows-x64-cuda")
RUNTIME_SCRIPTS = {
    "setup.sh",
    "install.sh",
    "start.sh",
    "install.ps1",
    "start.ps1",
    "launch.py",
    "choose_path.ps1",
    "setup-engines.sh",
    "setup-llama.sh",
    "setup_engines.py",
    "setup_ffmpeg.py",
    "download_release.py",
    "download_models.py",
    "release_assets.py",
}


def production_files(root):
    return [
        p
        for p in source_files(root)
        if p.parts[0] not in {"tests", ".github", "frontend"}
        and (p.parts[0] != "scripts" or p.name in RUNTIME_SCRIPTS)
    ]


def trim_engine(directory, engine, platform):
    for path in sorted(directory.rglob("*"), reverse=True):
        if not path.is_file() and not path.is_symlink():
            continue
        name = path.name
        keep = name == "ownsei-engine.json" or any(
            k in name.lower() for k in ("license", "notice", "copying")
        )
        if engine == "speech":
            keep |= name in ("speech", "speech.exe", "speech.dll") or ".so" in name
        elif platform == "windows":
            keep |= name == "llama-server.exe" or path.suffix.lower() == ".dll"
        else:
            # The server delegates to libllama-server-impl and can load optional
            # backends at runtime. Preserve the upstream shared-library set.
            keep |= name == "llama-server" or ".so" in name
        if not keep:
            path.unlink()
    # Server bundles need only their runtime dependencies, never conversion/CLI tools.


def build_release(root, output, target, cache, work):
    if target not in TARGETS:
        raise ValueError("Unsupported release target")
    files = production_files(root)
    issues = audit(root, files)
    if issues:
        raise ValueError(json.dumps(issues))
    if not (root / "frontend/dist/index.html").is_file():
        raise ValueError("Build the frontend first")
    platform, _, backend = target.split("-")
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    name = f"ownsei-{version}-{target}"
    output.mkdir(parents=True, exist_ok=True)
    result = output / (name + (".zip" if platform == "windows" else ".tar.gz"))
    if result.exists():
        raise ValueError("Release already exists: " + str(result))
    with tempfile.TemporaryDirectory(prefix="ownsei-package-", dir=work) as temporary:
        directory = Path(temporary) / name
        directory.mkdir()
        for relative in files:
            destination = directory / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, destination)
        shutil.copytree(root / "frontend/dist", directory / "frontend/dist")
        for engine, assets in engine_assets(platform, backend).items():
            install_engine(directory, engine, assets, platform, cache)
            trim_engine(directory / "bin" / engine, engine, platform)
        if backend != "cuda":
            (directory / "third_party/NVIDIA-CUDA-EULA.html").unlink(missing_ok=True)
        metadata = {
            "version": version,
            "platform": platform,
            "architecture": "x64",
            "backend": backend,
            "asr_backend": "vulkan",
            "python": "3.12",
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "engines": engine_assets(platform, backend),
        }
        (directory / "release.json").write_text(json.dumps(metadata, indent=2) + "\n")
        start = "Ownsei.cmd (Install.cmd for setup only)" if platform == "windows" else "bash ownsei.sh"
        instructions = f"""# Ownsei {version} · {target}

Start: `{start}`

The UI and native inference engines are included. First setup downloads Python
3.12 and application dependencies; Node.js is not required. Models are downloaded
from Model management after startup. User data stays in workspace/ and models/.

Haas uses speech.cpp Vulkan. Translation uses llama.cpp {backend.upper()}.
"""
        if platform == "windows":
            instructions += "\nRequires Windows 10/11 x64 and current GPU drivers. Setup downloads a pinned\nFFmpeg LGPL build directly from upstream if FFmpeg is not installed.\nWindows bundles require a native Windows smoke test before a stable release.\n"
        else:
            instructions += "\nRequires Linux x86-64, glibc >= 2.38, FFmpeg/ffprobe, curl and a Vulkan-capable\nGPU driver. First setup runs in this directory without changing the system PATH.\n"
        if backend == "cuda":
            instructions += "\nTranslation requires an NVIDIA driver compatible with CUDA 13.4. CUDA runtime\nlibraries are included under the NVIDIA CUDA EULA in third_party/.\n"
        instructions += "\n`release.json` records pinned inputs. `manifest.json` lists file SHA256 values.\n"
        (directory / "RELEASE.md").write_text(instructions, encoding="utf-8")
        hashes = {
            str(p.relative_to(directory)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*"))
            if p.is_file()
        }
        (directory / "manifest.json").write_text(json.dumps({"format": 1, "files": hashes}, indent=2) + "\n")
        if platform == "windows":
            with zipfile.ZipFile(result, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                for path in sorted(directory.rglob("*")):
                    if path.is_file():
                        bundle.write(path, str(path.relative_to(directory.parent)).replace("\\", "/"))
        else:
            with (
                result.open("xb") as raw,
                gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="", compresslevel=6) as compressed,
            ):
                with tarfile.open(fileobj=compressed, mode="w") as bundle:

                    def normalize(info):
                        info.mtime = 0
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        return info

                    bundle.add(directory, arcname=name, filter=normalize)
    with result.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"file": result.name, "bytes": result.stat().st_size, "sha256": sha, "target": target}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=TARGETS, action="append")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/release/platforms")
    parser.add_argument("--cache", type=Path, default=ROOT / "downloads")
    parser.add_argument("--work", type=Path, default=None)
    args = parser.parse_args()
    results = []
    for target in args.target or TARGETS:
        result = build_release(ROOT, args.output, target, args.cache, args.work)
        results.append(result)
        print(json.dumps(result), flush=True)
    (args.output / "SHA256SUMS").write_text("".join(f"{r['sha256']}  {r['file']}\n" for r in results))
    (args.output / "releases.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
