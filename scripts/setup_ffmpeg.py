"""Prepare a pinned Windows FFmpeg dependency, downloaded directly from upstream."""

from pathlib import Path
import shutil
import subprocess
import sys
from setup_engines import extract

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/autobuild-2026-11-22-12-36/ffmpeg-N-129219-g359c825152-win64-lgpl-shared.zip"
SHA256 = "a260df509347a4012ad2a19afd697f2e8902015a4f029beb4f7c046e00cbbf38"


def main():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    archive = ROOT / "downloads/ffmpeg-windows-lgpl.zip"
    archive.parent.mkdir(exist_ok=True)
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/download_release.py"), URL, str(archive), SHA256], check=True
    )
    directory = ROOT / "bin/ffmpeg"
    directory.mkdir(parents=True, exist_ok=True)
    extract(archive, directory)
    for tool in ["ffmpeg.exe", "ffprobe.exe"]:
        found = next(directory.rglob(tool))
        for file in found.parent.iterdir():
            if file.is_file():
                if file.resolve() != (directory / file.name).resolve():
                    shutil.copy2(file, directory / file.name)
        break


if __name__ == "__main__":
    main()
