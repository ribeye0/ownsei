"""Install hash-pinned Linux Vulkan engines with atomic directory replacement."""

import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = [
    (
        "nyosegawa/speech.cpp",
        "v0.11.2",
        "speech-0.11.2-linux-x64-vulkan.zip",
        "28e6841c650c8a4d7c5be06b8e34eb2f14d43f8b26d52fc73f24b504875dc12d",
        "speech",
        "speech",
    ),
    (
        "ggml-org/llama.cpp",
        "b11541",
        "llama-b11541-bin-ubuntu-vulkan-x64.tar.gz",
        "bf91ce14ddfda55c01c8b8846aa7d6093a07b0213913b6216ed4a2d6ea78b1f8",
        "llama",
        "llama-server",
    ),
]


def main():
    from onsei.util import file_hash, atomic_json
    from filelock import FileLock

    (ROOT / "bin").mkdir(exist_ok=True)
    (ROOT / "downloads").mkdir(exist_ok=True)
    with FileLock(ROOT / ".runtime-envs/engine-install.lock"):
        for repo, revision, filename, sha, engine, executable in ASSETS:
            target = ROOT / "bin" / engine
            receipt = target / "ownsei-engine.json"
            expected = {"release": revision, "archive_sha256": sha}
            if (
                receipt.exists()
                and json.loads(receipt.read_text()) == expected
                and next(target.rglob(executable), None)
            ):
                print(f"{engine} {revision}: ready", flush=True)
                continue
            archive = ROOT / "downloads" / filename
            old = ROOT / "downloads" / ("speech.zip" if engine == "speech" else "llama.tar.gz")
            if not archive.exists() and old.exists() and file_hash(old) == sha:
                archive = old
            subprocess.run(
                [
                    str(ROOT / ".venv/bin/python"),
                    str(ROOT / "scripts/download_release.py"),
                    f"https://github.com/{repo}/releases/download/{revision}/{filename}",
                    str(archive),
                    sha,
                ],
                check=True,
            )
            staging = Path(tempfile.mkdtemp(prefix=engine + "-", dir=ROOT / "bin"))
            try:
                if filename.endswith(".zip"):
                    with zipfile.ZipFile(archive) as package:
                        for member in package.infolist():
                            destination = (staging / member.filename).resolve()
                            if not destination.is_relative_to(staging.resolve()):
                                raise ValueError("Unsafe archive path")
                            package.extract(member, staging)
                            mode = member.external_attr >> 16
                            if mode:
                                destination.chmod(mode)
                else:
                    with tarfile.open(archive) as package:
                        package.extractall(staging, filter="data")
                if not next(staging.rglob(executable), None):
                    raise ValueError(f"Missing {executable} in release")
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
            print(f"{engine} {revision}: installed", flush=True)


if __name__ == "__main__":
    main()
