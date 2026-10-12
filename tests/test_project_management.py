import os
import sqlite3

from fastapi.testclient import TestClient

import onsei.api as service
from onsei.domain import Cue, Track
from onsei.storage import Store


def setup_project(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "REGISTRY", tmp_path / "index.db")
    client = TestClient(service.app, base_url="http://127.0.0.1")
    directory = tmp_path / "project"
    p = client.post("/api/projects", json={"title": "原標題", "path": str(directory)}).json()["id"]
    store = Store(directory)
    source = tmp_path / "original.wav"
    source.write_bytes(b"original remains")
    track = Track(
        id="t",
        name="track",
        source=str(source),
        sha256="hash",
        duration_ms=2000,
        sample_rate=16000,
        channels=1,
    )
    store.save_track(track)
    cue = Cue(
        id="c",
        track_id="t",
        version="v",
        ordinal=0,
        start_ms=0,
        end_ms=1000,
        ja_raw="はい。",
        zh_auto="好的。",
        zh_override="好！",
        translation_status="manual",
    )
    store.replace_cues(track, "v", [cue])
    (directory / "exports").mkdir()
    (directory / "exports/saved.jsonl").write_text("saved")
    return client, p, store, source


def test_rename_trash_restore_preserves_data_and_blocks_hidden_access(tmp_path, monkeypatch):
    client, p, store, source = setup_project(tmp_path, monkeypatch)
    before = store.cue("c").model_dump()
    url = f"/api/projects/{p}"
    assert client.patch(url + "/settings", json={"title": "  新標題  "}).status_code == 200
    assert client.get("/api/projects").json()[0]["title"] == "新標題"
    assert store.cue("c").model_dump() == before  # Renaming does not invalidate translation.
    assert client.delete(url).status_code == 200
    assert client.get("/api/projects").json() == []
    trash = client.get("/api/projects?trashed=true").json()
    assert len(trash) == 1 and trash[0]["id"] == p and trash[0]["trashed_at"] > 0
    assert client.get(url).status_code == 410
    assert client.patch(url + "/settings", json={"title": "hidden"}).status_code == 410
    assert client.post(url + "/jobs", json={"action": "transcribe"}).status_code == 410
    assert client.post("/api/projects", json={"title": "reopen", "path": str(store.path)}).status_code == 409
    assert client.delete(url).status_code == 200  # Idempotent, retaining the deletion date.
    assert store.cue("c").model_dump() == before
    assert source.read_bytes() == b"original remains"
    assert (store.path / "exports/saved.jsonl").read_text() == "saved"
    assert client.post(url + "/restore").status_code == 200
    assert client.get("/api/projects?trashed=true").json() == []
    assert client.get(url).json()["settings"]["title"] == "新標題"
    assert client.get(url + "/tracks/t/cues").json()[0]["zh"] == "好！"
    assert store.track("t").active_version == "v"


def test_delete_blocks_all_active_jobs_including_older_than_recent_list(tmp_path, monkeypatch):
    client, p, store, _ = setup_project(tmp_path, monkeypatch)
    active = store.new_job("transcribe", {})
    active.update(status="running", pid=os.getpid())
    store.save_job(active)
    for _ in range(51):
        completed = store.new_job("transcribe", {})
        completed["status"] = "completed"
        store.save_job(completed)
    assert active["id"] not in {j["id"] for j in store.jobs()}
    assert client.delete(f"/api/projects/{p}").status_code == 409
    assert len(client.get("/api/projects").json()) == 1
    active["status"] = "cancelled"
    store.save_job(active)
    assert client.delete(f"/api/projects/{p}").status_code == 200


def test_title_validation_and_existing_registry_migration(tmp_path, monkeypatch):
    path = tmp_path / "existing"
    Store.create(path, "既有作品")
    index = tmp_path / "index.db"
    with sqlite3.connect(index) as db:
        db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,path TEXT UNIQUE)")
        db.execute("INSERT INTO projects VALUES (?,?)", ("old", str(path)))
    monkeypatch.setattr(service, "REGISTRY", index)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    assert client.get("/api/projects").json()[0]["id"] == "old"
    for title in ("", "   ", None, 12, "長" * 201):
        assert client.patch("/api/projects/old/settings", json={"title": title}).status_code == 422
    assert client.post("/api/projects", json={"title": "   "}).status_code == 422
    assert client.delete("/api/projects/old").status_code == 200
    assert client.post("/api/projects/old/restore").status_code == 200
    assert client.delete("/api/projects/unknown").status_code == 404
