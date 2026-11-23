"""Audit and build a reproducible source bundle from an explicit public-file inventory."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    ".gitignore",
    "pyproject.toml",
    "uv.lock",
    "ownsei.sh",
    "Ownsei.cmd",
    "Install.cmd",
)
FRONTEND_FILES = ("index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts")
DIRECTORIES = {
    "src/onsei": {".py"},
    "tests": {".py"},
    "frontend/src": {".ts", ".tsx", ".css", ".json"},
    "frontend/tests": {".mjs"},
    "scripts": {".py", ".sh", ".ps1", ".bat", ".cmd"},
    ".github/workflows": {".yml", ".yaml"},
}
RUNTIMES = ("vad", "vad-windows")
ASSETS = ("ownsei-logo.png", "ownsei-logo-dark.png")
SECRETS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|hf_[A-Za-z0-9]{20,}|sk-(?:or-v1-)?[A-Za-z0-9_-]{32,})\b"),
)
PRIVATE_PATH = re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/|/(?:root)/(?!\.cache)")


def source_files(root: Path) -> list[Path]:
    files = [Path(name) for name in ROOT_FILES]
    files += [Path("frontend") / name for name in FRONTEND_FILES]
    files += [Path("frontend/public") / name for name in ASSETS]
    files += [Path("runtimes") / f"{kind}.{suffix}" for kind in RUNTIMES for suffix in ("in", "lock")]
    for name, suffixes in DIRECTORIES.items():
        files += [
            p.relative_to(root)
            for p in (root / name).rglob("*")
            if (p.is_file() or p.is_symlink()) and p.suffix in suffixes and "__pycache__" not in p.parts
        ]
    files += [p.relative_to(root) for p in (root / "third_party").glob("*") if p.is_file()]
    return sorted(set(files))


def audit(root: Path, files: list[Path]) -> list[dict]:
    issues = []
    for relative in files:
        path = root / relative
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p.is_relative_to(root)):
            issues.append({"file": str(relative), "reason": "symlink"})
        elif not path.is_file():
            issues.append({"file": str(relative), "reason": "missing required file"})
        elif path.suffix != ".png":
            try:
                value = path.read_text(encoding="utf-8")
            except UnicodeError:
                issues.append({"file": str(relative), "reason": "unexpected binary"})
                continue
            if PRIVATE_PATH.search(value):
                issues.append({"file": str(relative), "reason": "private absolute path"})
            if any(pattern.search(value) for pattern in SECRETS):
                issues.append({"file": str(relative), "reason": "possible credential"})
    return issues


def package(root: Path, output: Path, *, source_commit: str | None = None) -> dict:
    files = source_files(root)
    issues = audit(root, files)
    if issues:
        raise ValueError(json.dumps(issues, ensure_ascii=False))
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    prefix = f"ownsei-{version}-source"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (prefix + ".tar.gz")
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    hashes = {p.as_posix(): hashlib.sha256((root / p).read_bytes()).hexdigest() for p in files}
    manifest = json.dumps(
        {
            "format": 1,
            "version": version,
            **({"source_commit": source_commit} if source_commit else {}),
            "files": hashes,
        },
        ensure_ascii=False,
        indent=2,
    ).encode()
    # Exclusive creation keeps an already reviewed bundle immutable.
    try:
        stream = archive.open("xb")
    except FileExistsError:
        raise ValueError(f"Bundle already exists: {archive}") from None
    try:
        with stream, gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as bundle:
                for relative, content in [(p, (root / p).read_bytes()) for p in files] + [
                    (Path("manifest.json"), manifest)
                ]:
                    info = tarfile.TarInfo(prefix + "/" + relative.as_posix())
                    info.size, info.mtime = len(content), epoch
                    info.mode = 0o755 if relative.suffix == ".sh" else 0o644
                    bundle.addfile(info, io.BytesIO(content))
    except BaseException:
        archive.unlink(missing_ok=True)
        raise
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / (archive.name + ".sha256")).write_text(f"{checksum}  {archive.name}\n")
    return {"archive": str(archive), "sha256": checksum, "files": len(files), "version": version}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="audit without building an archive")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/release")
    args = parser.parse_args()
    files = source_files(ROOT)
    issues = audit(ROOT, files)
    if args.check:
        print(json.dumps({"files": len(files), "issues": issues}, ensure_ascii=False, indent=2))
        raise SystemExit(bool(issues))
    try:
        print(json.dumps(package(ROOT, args.output), ensure_ascii=False, indent=2))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
