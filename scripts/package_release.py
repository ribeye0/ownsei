"""Build a source archive from the public project files."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"README.md", "THIRD_PARTY_NOTICES.md", ".gitignore", "pyproject.toml", "uv.lock", "LICENSE"}
SCRIPTS = {"install.sh", "setup.sh", "setup-llama.sh", "start.sh", "download_release.py", "download_models.py", "cache_wheels.py", "package_release.py"}
DOCS = {"deployment.md"}
SECRET_PATTERNS = (r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----", r"gh[pousr]_[A-Za-z0-9]{30,}", r"hf_[A-Za-z0-9]{30,}")
PRIVATE_PATH = re.compile(r"/(?:home|Users)/[^/\s\"']+/")


def release_paths(root=ROOT):
    for name in sorted(ROOT_FILES):
        if (root / name).is_file():
            yield Path(name)
    for directory in ("src", "tests", "runtimes", "third_party", ".github", "deploy"):
        for path in sorted((root / directory).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if directory == "runtimes" and path.suffix not in (".in", ".lock"):
                continue
            yield path.relative_to(root)
    for name in sorted(SCRIPTS):
        yield Path("scripts") / name
    for name in sorted(DOCS):
        if (root / "docs" / name).is_file():
            yield Path("docs") / name
    for name in ("src", "tests"):
        for path in sorted((root / "frontend" / name).rglob("*")):
            if path.is_file():
                yield path.relative_to(root)
    for name in ("index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts"):
        if (root / "frontend" / name).is_file():
            yield Path("frontend") / name


def audit_files(root, paths):
    issues = []
    for relative in paths:
        path = root / relative
        if path.is_symlink():
            issues.append({"file": str(relative), "reason": "symlink"})
            continue
        text = path.read_text(encoding="utf-8")
        if PRIVATE_PATH.search(text):
            issues.append({"file": str(relative), "reason": "private local path"})
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, text):
                issues.append({"file": str(relative), "reason": "possible credential"})
    return issues


def package(output):
    if output.exists():
        raise ValueError("Output directory already exists")
    paths = list(release_paths())
    issues = audit_files(ROOT, paths)
    if issues:
        raise ValueError(json.dumps(issues, ensure_ascii=False))
    source = output / "Ownsei-0.1.0"
    source.mkdir(parents=True)
    hashes = {}
    for relative in paths:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
        hashes[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
    report = {"files": hashes, "file_count": len(hashes), "includes_git_history": False,
              "license_selected": (source / "LICENSE").exists()}
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = output / "Ownsei-0.1.0-source.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname=source.name)
    return {"source": str(source), "archive": str(archive), "files": len(hashes), "license_selected": report["license_selected"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/release/candidate")
    args = parser.parse_args()
    print(json.dumps(package(args.output.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
