"""Install hash-pinned speech.cpp Vulkan and llama.cpp CUDA/Vulkan engines."""

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from release_assets import engine_assets

ROOT = Path(__file__).resolve().parents[1]


def receipt(assets):
    return {"archives": {a["name"]: a["sha256"] for a in assets}}


def extract(archive, target):
    if archive.suffix in (".zip", ".whl"):
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                destination = (target / member.filename).resolve()
                if not destination.is_relative_to(target.resolve()):
                    raise ValueError("Unsafe archive path")
                package.extract(member, target)
                mode = member.external_attr >> 16
                if mode and sys.platform != "win32":
                    destination.chmod(mode)
    else:
        with tarfile.open(archive) as package:
            package.extractall(target, filter="data")


def install_engine(root, engine, assets, platform, cache):
    from onsei.util import file_hash, atomic_json
    from onsei.catalog import vad_libraries

    target = root / "bin" / engine
    executable = ("speech" if engine == "speech" else "llama-server") + (
        ".exe" if platform == "windows" else ""
    )

    def complete(directory):
        if engine == "vad":
            return all((directory / path).is_file() for path in vad_libraries(platform))
        return next(directory.rglob(executable), None) is not None

    expected = receipt(assets)
    marker = target / "ownsei-engine.json"
    if (
        marker.exists()
        and json.loads(marker.read_text()) == expected
        and complete(target)
    ):
        print(engine + ": ready", flush=True)
        return
    cache.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=engine + "-", dir=target.parent))
    try:
        for asset in assets:
            archive = cache / asset["name"]
            if not archive.is_file() or file_hash(archive) != asset["sha256"]:
                subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts/download_release.py"),
                        asset["url"],
                        str(archive),
                        asset["sha256"],
                    ],
                    check=True,
                )
            extract(archive, staging)
        if not complete(staging):
            raise ValueError("Missing engine files: " + engine)
        atomic_json(staging / "ownsei-engine.json", expected)
        previous = target.with_name(engine + ".previous")
        if previous.exists():
            shutil.rmtree(previous)
        if target.exists():
            target.replace(previous)
        try:
            staging.replace(target)
        except BaseException:
            if previous.exists():
                previous.replace(target)
            raise
        if previous.exists():
            shutil.rmtree(previous)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(engine + ": installed", flush=True)


def main():
    from filelock import FileLock

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["cuda", "vulkan"])
    parser.add_argument("--engine", choices=("speech", "llama", "vad"))
    args = parser.parse_args()
    profile = json.loads((ROOT / "release.json").read_text()) if (ROOT / "release.json").exists() else {}
    platform = "windows" if sys.platform == "win32" else "linux"
    backend = args.backend or profile.get("backend", "vulkan")
    (ROOT / ".runtime-envs").mkdir(exist_ok=True)
    with FileLock(ROOT / ".runtime-envs/engine-install.lock"):
        for engine, assets in engine_assets(platform, backend).items():
            if args.engine and engine != args.engine:
                continue
            install_engine(ROOT, engine, assets, platform, ROOT / "downloads")


if __name__ == "__main__":
    main()
