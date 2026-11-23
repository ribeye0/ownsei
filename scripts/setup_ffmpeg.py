"""Install just the Windows FFmpeg tools, their shared libraries and licences."""

from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/autobuild-2026-11-22-12-36/ffmpeg-N-129219-g359c825152-win64-lgpl-shared.zip"
SHA256 = "a260df509347a4012ad2a19afd697f2e8902015a4f029beb4f7c046e00cbbf38"
TOOLS = ("ffmpeg.exe", "ffprobe.exe")


def extract_tools(archive, directory):
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            if member.is_dir():
                continue
            path = PurePosixPath(member.filename)
            if ".." in path.parts or path.is_absolute() or "\\" in member.filename or ":" in member.filename:
                raise ValueError("Invalid archive member")
            if path.parent.name == "bin" and (path.name in TOOLS or path.suffix.lower() == ".dll"):
                target = directory / path.name
            elif any(
                part.lower().startswith(("license", "licence", "copying", "notice")) for part in path.parts
            ):
                target = directory / "licenses" / Path(*path.parts)
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open("xb") as dest:
                shutil.copyfileobj(source, dest)
    if not all((directory / tool).is_file() for tool in TOOLS):
        raise ValueError("Pinned FFmpeg archive lacks a required executable")


def works(commands):
    try:
        return all(
            command and subprocess.run([command, "-version"], capture_output=True, timeout=15).returncode == 0
            for command in commands
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def main():
    if works([shutil.which(tool) for tool in TOOLS]):
        return
    directory = ROOT / "bin/ffmpeg"
    archive = ROOT / "downloads/ffmpeg-windows-lgpl.zip"
    if works([directory / tool for tool in TOOLS]):
        return
    archive.parent.mkdir(exist_ok=True)
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/download_release.py"), URL, str(archive), SHA256], check=True
    )
    directory.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory.parent) as temp:
        stage = Path(temp) / "ffmpeg"
        stage.mkdir()
        extract_tools(archive, stage)
        if not works([stage / tool for tool in TOOLS]):
            raise RuntimeError("FFmpeg/ffprobe verification failed")
        directory.mkdir(exist_ok=True)
        for file in stage.rglob("*"):
            if file.is_file():
                target = directory / file.relative_to(stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                file.replace(target)
    if not works([directory / tool for tool in TOOLS]):
        raise RuntimeError("Installed FFmpeg verification failed")
    archive.unlink()


if __name__ == "__main__":
    main()
