"""Install verified release files in place while the server is stopped."""

import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from filelock import ReadWriteLock

from onsei.util import atomic_json, file_hash


def version_key(version):
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError("無效的正式版本號")
    return tuple(map(int, version.split(".")))


def target_of(metadata):
    target = "-".join(metadata[k] for k in ("platform", "architecture", "backend"))
    if not re.fullmatch(r"(linux|windows)-x64-(vulkan|cuda)", target):
        raise ValueError("沒有適用的更新包")
    return target


def install_gate(root):
    return ReadWriteLock(update_dir(root) / "instance.lock", timeout=0)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def update_dir(root):
    return root / ".runtime-envs" / "update"


def managed(name):
    parts = name.split("/")
    if (
        any(p in ("", ".", "..") or p.rstrip(" .") != p or PureWindowsPath(p).is_reserved() for p in parts)
        or "\\" in name
        or ":" in name
    ):
        raise ValueError("無效的更新檔案路徑")
    roots = {"docs", "scripts", "src", "third_party", "frontend", "bin", "runtimes"}
    files = {
        ".gitignore",
        "README.md",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "pyproject.toml",
        "uv.lock",
        "ownsei.sh",
        "Ownsei.cmd",
        "Install.cmd",
        "Uninstall.cmd",
        "uninstall.sh",
        "release.json",
        "manifest.json",
        "RELEASE.md",
    }
    if not (len(parts) > 1 and parts[0] in roots or name in files):
        raise ValueError(f"更新清單包含非程式檔案：{name}")
    return name


def unpack(archive, directory, prefix):
    if "-windows-" in prefix:
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                if entry.is_dir():
                    continue
                if not entry.filename.startswith(prefix + "/"):
                    raise ValueError("更新包目錄不符")
                path = directory / managed(entry.filename[len(prefix) + 1 :])
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as src, path.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            for entry in bundle:
                if entry.name == prefix and entry.isdir():
                    continue
                if not entry.name.startswith(prefix + "/"):
                    raise ValueError("更新包目錄不符")
                relative = entry.name[len(prefix) + 1 :]
                if entry.isdir():
                    continue
                managed(relative)
                # Engine bundles contain relative, same-directory shared-library links.
                if entry.issym():
                    if not relative.startswith("bin/") or "/" in entry.linkname:
                        raise ValueError("更新包包含無效的引擎連結")
                    managed(relative.rsplit("/", 1)[0] + "/" + entry.linkname)
                elif not entry.isfile():
                    raise ValueError("更新包包含無效的檔案類型")
                bundle.extract(entry, directory.parent, filter="data")


def file_list(directory):
    values = read(directory / "manifest.json")["files"]
    return {managed(k): v for k, v in values.items()} | {
        "manifest.json": file_hash(directory / "manifest.json")
    }


def copy_atomic(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        copy = Path(tmp) / "file"
        shutil.copy2(source, copy, follow_symlinks=False)
        os.replace(copy, destination)
    if destination.suffix == ".py":
        for cached in (destination.parent / "__pycache__").glob(destination.stem + ".*.pyc"):
            cached.unlink(missing_ok=True)


def rollback(root):
    backup = update_dir(root) / "backup"
    journal = backup / "plan.json"
    if not journal.is_file():
        return False
    for name, existed in read(journal).items():
        dest = root / managed(name)
        if existed:
            copy_atomic(backup / "files" / name, dest)
        else:
            dest.unlink(missing_ok=True)
    journal.unlink()
    shutil.rmtree(backup)
    return True


def apply(root):
    cache = update_dir(root)
    offer = read(cache / "ready.json")
    current = read(root / "release.json")
    target = target_of(current)
    version = offer["version"]
    if target != offer["target"] or version_key(version) <= version_key(current["version"]):
        raise ValueError("更新包與目前版本不相容")
    archive = cache / "package"
    if file_hash(archive) != offer["sha256"]:
        (cache / "ready.json").unlink(missing_ok=True)
        raise ValueError("更新包 SHA-256 驗證失敗")
    prefix = f"ownsei-{version}-{target}"
    with tempfile.TemporaryDirectory(dir=cache) as temp:
        stage = Path(temp) / prefix
        stage.mkdir()
        unpack(archive, stage, prefix)
        metadata = read(stage / "release.json")
        if metadata["version"] != version or target_of(metadata) != target:
            raise ValueError("更新包版本或平台不符")
        old, new = file_list(root), file_list(stage)
        if any(file_hash(stage / name) != digest for name, digest in new.items()):
            raise ValueError("更新檔案校驗失敗")
        backup = cache / "backup"
        if (backup / "plan.json").exists():
            raise ValueError("上次更新仍需還原")
        shutil.rmtree(backup, ignore_errors=True)
        plan = {}
        for name in sorted(old.keys() | new.keys()):
            dest = root / name
            if any(p.is_symlink() or p.is_junction() for p in dest.parents if p.is_relative_to(root)):
                raise ValueError(f"更新路徑已重新導向：{name}")
            exists = dest.exists() or dest.is_symlink()
            if exists and name not in old:
                raise ValueError(f"安裝目錄已有同名檔案：{name}")
            staged = stage / name
            if name in new and old.get(name) == new[name] and exists and file_hash(dest) == new[name]:
                if dest.is_symlink() and staged.is_symlink() and os.readlink(dest) == os.readlink(staged):
                    continue
                if (
                    not dest.is_symlink()
                    and not staged.is_symlink()
                    and stat.S_IMODE(dest.stat().st_mode) == stat.S_IMODE(staged.stat().st_mode)
                ):
                    continue
            if exists:
                saved = backup / "files" / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, saved, follow_symlinks=False)
            plan[name] = exists
        atomic_json(backup / "plan.json", plan)
        for name in plan:
            if name in new:
                copy_atomic(stage / name, root / name)
            else:
                (root / name).unlink(missing_ok=True)


def setup(root):
    command = (
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/install.ps1"]
        if sys.platform == "win32"
        else ["bash", "scripts/install.sh"]
    )
    subprocess.run(command, cwd=root, check=True)
