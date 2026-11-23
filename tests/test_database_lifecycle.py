"""SQLite handles must close before database rename, copy cleanup or deletion."""

from contextlib import closing
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from onsei import api, modelmanager, project_copy
from onsei.storage import Store
from onsei.util import file_hash


@pytest.fixture
def handles(monkeypatch):
    opened = []
    connect = sqlite3.connect
    replace = Path.replace

    class Connection(sqlite3.Connection):
        closed = False

        def close(self):
            super().close()
            self.closed = True

    def tracked(*args, **kwargs):
        db = connect(*args, **kwargs, factory=Connection)
        db.filename = Path(db.execute("PRAGMA database_list").fetchone()[2]).resolve()
        opened.append(db)  # Hold references so garbage collection cannot hide leaks.
        return db

    def windows_replace(path, target):
        if any(not db.closed and db.filename == path.resolve() for db in opened):
            raise PermissionError("WinError 32: database is still open")
        return replace(path, target)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    monkeypatch.setattr(Path, "replace", windows_replace)
    yield opened
    for db in opened:
        if not db.closed:
            db.close()


def test_project_create_list_and_permanent_delete_release_database_files(tmp_path, monkeypatch, handles):
    monkeypatch.setattr(api, "ROOT", tmp_path)
    monkeypatch.setattr(api, "REGISTRY", tmp_path / "workspace/index.db")
    client = TestClient(api.app, base_url="http://127.0.0.1")
    response = client.post("/api/projects", json={"title": "新專案"})
    assert response.status_code == 200, response.text
    identity = response.json()["id"]
    rows = client.get("/api/projects").json()
    assert rows[0]["id"] == identity and rows[0]["title"] == "新專案"
    project = Path(rows[0]["path"])
    assert not list(project.glob("*.tmp.db"))
    assert client.delete(f"/api/projects/{identity}").status_code == 200
    response = client.delete(f"/api/projects/{identity}/purge")
    assert response.status_code == 200, response.text
    assert not project.exists()
    assert handles and all(db.closed for db in handles)


def test_retry_after_interrupted_migration_preserves_data_and_valid_backup(tmp_path, handles):
    store = Store.create(tmp_path / "project", "保留的專案")
    with store.db() as db:
        db.execute("DROP TABLE translation_runs")
        db.execute("PRAGMA user_version=3")
    backup = store.path / "project-before-translation-runs.db"
    backup.unlink()
    temporary = backup.with_suffix(".tmp.db")
    with store.db() as db, closing(sqlite3.connect(temporary)) as target:
        db.backup(target)
    reopened = Store(store.path)
    assert reopened.settings()["title"] == "保留的專案"
    with reopened.db() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert not temporary.exists()
    assert all(db.closed for db in handles)


def test_copy_closes_source_and_target_and_commits_job_cleanup(tmp_path, handles):
    original = Store.create(tmp_path / "原始 # 專案", "原始資料")
    original.new_job("translate", {})
    before = file_hash(original.path / "project.db")
    copied = project_copy.copy_project(original.path, tmp_path / "複製專案")
    assert copied.settings()["title"] == "原始資料"
    assert copied.jobs() == []
    assert len(original.jobs()) == 1
    assert file_hash(original.path / "project.db") == before
    assert all(db.closed for db in handles)


def test_failed_copy_can_remove_destination_without_open_database_handles(tmp_path, monkeypatch, handles):
    original = Store.create(tmp_path / "original", "source")
    destination = tmp_path / "copy"
    monkeypatch.setattr(project_copy, "Store", lambda _: (_ for _ in ()).throw(ValueError("invalid copy")))
    with pytest.raises(ValueError, match="invalid copy"):
        project_copy.copy_project(original.path, destination)
    assert not destination.exists()
    assert all(db.closed for db in handles)


def test_model_management_closes_workspace_index(tmp_path, monkeypatch, handles):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with closing(sqlite3.connect(workspace / "index.db")) as db:
        db.execute("CREATE TABLE projects(path TEXT)")
    monkeypatch.setattr(modelmanager, "ROOT", tmp_path)
    assert not modelmanager.tasks_pending()
    assert all(db.closed for db in handles)
