"""Local audio picker and conservative project removal helpers."""

import shutil
from pathlib import Path

from onsei.audio import FORMATS
from onsei.util import natural_key


def browse_audio(path=None):
    directory = Path(path).expanduser() if path else Path.home()
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        directory = directory.parent
    entries = []
    for item in directory.iterdir():
        try:
            if item.name.startswith("."):
                continue
            folder = item.is_dir()
            if folder or (item.is_file() and item.suffix.lower() in FORMATS):
                entries.append({"name": item.name, "path": str(item), "directory": folder})
        except OSError:
            continue
    entries.sort(key=lambda item: (not item["directory"], natural_key(item["name"])))
    return {"path": str(directory), "parent": str(directory.parent), "entries": entries}


def removal_scope(store, identity, workspace):
    # Only the exact default workspace directory is owned by the application.
    owned = store.path == (workspace / identity).absolute() and not (workspace / identity).is_symlink()
    if owned:
        for track in store.tracks():
            if Path(track.source).resolve().is_relative_to(store.path):
                raise ValueError("原始音檔位於工作目錄內，已阻止永久刪除；請先移出原音")
        allowed = {
            "project.db",
            "project.db-wal",
            "project.db-shm",
            "cache",
            "audio",
            "exports",
            "logs",
            "trash",
            "import-checksums.json",
            "imports",
        }
        unknown = [
            p.name
            for p in store.path.iterdir()
            if p.name not in allowed
            and not (p.name.startswith(".") and p.name.endswith(".lock"))
            and not (p.name.startswith("project-before-") and p.suffix == ".db")
        ]
        if unknown:
            raise ValueError("工作目錄包含其他檔案，已阻止永久刪除：" + ", ".join(unknown[:5]))
    return "workspace" if owned else "registration"


def remove_workspace(store):
    # Keep the database until cache/output cleanup succeeds so a failed deletion
    # remains visible in the trash and can be retried. Original sources are checked first.
    for item in store.path.iterdir():
        if item.name == "project.db":
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()
    (store.path / "project.db").unlink()
    store.path.rmdir()
