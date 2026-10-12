import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import onsei.api as service
import onsei.importjobs as jobs
from onsei.storage import Store


def wait(store, identity):
    for _ in range(200):
        result = jobs.read_import(store, identity)
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("import did not finish")


def test_background_progress_conflict_and_project_protection(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "REGISTRY", tmp_path / "index.db")
    entered, release = threading.Event(), threading.Event()

    def slow_import(store, source, progress):
        progress({"stage": "hashing", "files_total": 2, "bytes_total": 100, "bytes_done": 50})
        entered.set()
        assert release.wait(5)
        progress({"stage": "completed", "files_done": 2, "bytes_done": 100, "added": 1, "skipped": 1})

    monkeypatch.setattr(jobs, "import_audio", slow_import)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    p = client.post("/api/projects", json={"title": "匯入", "path": str(tmp_path / "project")}).json()["id"]
    store = service.project(p)
    try:
        response = client.post(f"/api/projects/{p}/imports", json={"path": "/source"})
        assert response.status_code == 200
        identity = response.json()["id"]
        assert entered.wait(2)
        status = client.get(f"/api/projects/{p}/imports/{identity}").json()
        assert status["bytes_done"] == 50 and status["status"] == "running"
        assert client.get(f"/api/projects/{p}/imports").json()["id"] == identity
        assert client.post(f"/api/projects/{p}/imports", json={"path": "/other"}).status_code == 422
        assert client.delete(f"/api/projects/{p}").status_code == 409
        assert client.get(f"/api/projects/{p}/imports/invalid").status_code == 404
    finally:
        release.set()
    finished = wait(store, identity)
    assert finished["status"] == "completed" and finished["added"] == finished["skipped"] == 1
    assert client.delete(f"/api/projects/{p}").status_code == 200


def test_failed_import_preserves_partial_progress_and_restart_is_reported(tmp_path, monkeypatch):
    store = Store.create(tmp_path / "project", "import")

    def broken(store, source, progress):
        progress({"files_done": 1, "added": 1, "current_file": "bad.wav", "stage": "probing"})
        raise ValueError("broken codec")

    monkeypatch.setattr(jobs, "import_audio", broken)
    identity = jobs.start_import(store, tmp_path)["id"]
    failed = wait(store, identity)
    assert failed["status"] == "failed" and failed["added"] == 1 and failed["error"] == "broken codec"
    path = store.path / "imports" / f"{identity}.json"
    failed.update(status="running", pid=99999999)
    path.write_text(json.dumps(failed))
    restarted = jobs.read_import(store, identity)
    assert restarted["status"] == "interrupted" and restarted["added"] == 1
    with pytest.raises(ValueError):
        jobs.read_import(store, "../../outside")


def test_real_background_import_reopens_saved_progress(tmp_path):
    import wave

    source = tmp_path / "voice.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    store = Store.create(tmp_path / "project", "real import")
    identity = jobs.start_import(store, source)["id"]
    result = wait(store, identity)
    assert result["status"] == result["stage"] == "completed"
    assert result["added"] == result["files_done"] == 1
    assert result["bytes_read"] == result["bytes_total"] == source.stat().st_size
    assert jobs.latest_import(Store(store.path))["id"] == identity
    second = wait(store, jobs.start_import(store, source)["id"])
    assert second["cached"] == second["skipped"] == 1 and second["bytes_read"] == 0
    assert len(store.tracks()) == 1
