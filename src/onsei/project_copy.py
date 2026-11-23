"""Copy a project snapshot without attaching to or migrating the original database."""

import sqlite3
from contextlib import closing
from pathlib import Path
from onsei.storage import Store


def copy_project(source: Path, destination: Path) -> Store:
    source, destination = Path(source).resolve(), Path(destination).resolve()
    database = source / "project.db"
    if not database.is_file():
        raise ValueError("來源目錄沒有 Ownsei project.db")
    destination.mkdir(parents=True, exist_ok=False)
    uri = database.as_uri() + "?mode=ro"
    try:
        with (
            closing(sqlite3.connect(uri, uri=True)) as original,
            closing(sqlite3.connect(destination / "project.db")) as copied,
            copied,
        ):
            original.backup(copied)
            # Old PIDs and queued jobs belong to the original installation.
            if copied.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                copied.execute("DELETE FROM jobs")
        return Store(destination)
    except BaseException:
        import shutil

        shutil.rmtree(destination)
        raise
