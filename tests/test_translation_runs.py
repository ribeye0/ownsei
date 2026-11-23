import json

import pytest

from onsei.domain import Cue, Track
from onsei.storage import Store
from onsei.translation_runs import TranslationSession


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "project", "runs")
    t = Track(
        id="t", name="dialogue", source="/fake", sha256="h", duration_ms=10000, sample_rate=16000, channels=1
    )
    s.save_track(t)
    s.replace_cues(
        t,
        "v",
        [
            Cue(
                id=f"c{i}",
                track_id="t",
                version="v",
                ordinal=i,
                start_ms=i * 1000,
                end_ms=i * 1000 + 900,
                ja_raw="はい",
            )
            for i in range(3)
        ],
    )
    return s


def test_new_source_or_human_edit_cannot_be_overwritten(store):
    snapshot = store.input_snapshot("t")
    identity = store.new_translation_run(snapshot, "index-translate-9b")
    session = TranslationSession(store, snapshot, identity)
    cues = session.cues("t")
    store.edit("c0", {"zh_override": "人工"})
    cues[0].zh_auto = "模型"
    assert not session.save_generated(cues[0], cues[0].revision)
    assert store.cue("c0").zh == "人工"
    store.edit("c1", {"ja_override": "いいえ"})
    with pytest.raises(ValueError, match="來源已更新"):
        session.save_generated(cues[2], cues[2].revision)
    with pytest.raises(ValueError, match="來源已更新"):
        store.new_translation_run(snapshot, "index-translate-9b")


def test_review_clones_manual_and_survives_parent_deletion(store):
    parent = store.prepare_translation("t", "index-translate-9b")
    for cue in store.cues("t"):
        cue.zh_auto, cue.translation_status = "好的", "translated"
        store.save_generated(cue, cue.revision)
    store.edit("c0", {"zh_override": "人工"})
    snapshot = store.input_snapshot("t")
    child = store.new_translation_run(snapshot, "index-translate-9b", stage="review")
    assert child != parent and store.cue("c0").zh == "人工"
    store.delete_translation("t", parent)
    assert store.cue("c1").zh == "好的"
    assert store.run_metadata(child)["parent_result_id"] == parent
    assert store.input_snapshot("t")["result_id"] == child


def test_schema_backup_is_valid_and_checkpoint_checks_source(store):
    import sqlite3

    assert (store.path / "project-before-translation-runs.db").exists()
    with sqlite3.connect(store.path / "project-before-translation-runs.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    snap = store.input_snapshot("t")
    run = store.new_translation_run(snap, "index-translate-9b", identity="task-run")
    assert store.new_translation_run(snap, "index-translate-9b", identity="task-run") == run
    with store.db() as db:
        assert json.loads(db.execute("SELECT payload FROM translation_runs").fetchone()[0])["coverage"] == []


def test_batch_preserves_manual_override_without_rescanning_track(store, monkeypatch):
    snapshot = store.input_snapshot("t")
    result = store.new_translation_run(snapshot, "index-nativelong-9b")
    session = TranslationSession(store, snapshot, result)
    cues = session.cues("t")
    for cue in cues:
        cue.zh_auto, cue.translation_status = "模型", "translated"
    store.edit("c1", {"zh_override": "人工"})
    monkeypatch.setattr(store, "snapshot_valid", lambda *a, **kw: pytest.fail("reloaded transcript"))
    assert session.save_generated_batch(cues) == {"c0", "c2"}
    assert [cue.zh for cue in store.cues("t")] == ["模型", "人工", "模型"]


def test_incomplete_batch_rolls_back_rows_and_revisions(store):
    snapshot = store.input_snapshot("t")
    result = store.new_translation_run(snapshot, "index-nativelong-9b")
    session = TranslationSession(store, snapshot, result)
    cues = session.cues("t")
    for cue in cues:
        cue.zh_auto, cue.translation_status = "初稿", "translated"
    assert session.save_generated_batch(cues[:1]) == {"c0"}
    before = store.cue("c1").revision
    missing = cues[2].model_copy(update={"id": "unknown"})
    with pytest.raises(ValueError, match="deleted"):
        session.save_generated_batch([cues[1], missing])
    assert store.cue("c1").revision == before
    assert store.cue("c1").zh is None
    assert session.save_generated_batch(cues[1:]) == {"c1", "c2"}


@pytest.mark.parametrize("status", ["queued", "waiting", "running", "cancelling"])
def test_project_is_read_only_until_last_model_job_ends(store, status):
    from onsei.storage import ConflictError

    first = store.new_job("run", {})
    second = store.new_job("transcribe", {})
    first["status"] = "completed"
    store.save_job(first)
    second["status"] = status
    store.save_job(second)
    with pytest.raises(ConflictError, match="字幕暫時唯讀"):
        store.edit("c0", {"start_ms": 1, "zh_override": "稍後校訂"})
    assert store.cue("c0").start_ms == 0
    second["status"] = "cancelled"
    store.save_job(second)
    store.new_job("export", {})
    assert store.edit("c0", {"zh_override": "校訂完成"}).zh == "校訂完成"


def test_cue_api_is_read_only_for_active_model_jobs_only(store, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from onsei import api

    idle_project = Store.create(tmp_path / "other", "Other")
    track = store.track("t")
    idle_project.save_track(track)
    idle_project.replace_cues(track, "v", store.cues("t"))
    monkeypatch.setattr(api, "project", {"busy": store, "idle": idle_project}.__getitem__)
    client = TestClient(api.app, base_url="http://127.0.0.1")
    body = {"changes": {"ja_override": "変更"}, "revision": store.cue("c0").revision}
    job = store.new_job("translate", {})
    assert client.patch("/api/projects/busy/cues/c0", json=body).status_code == 409
    assert client.patch("/api/projects/idle/cues/c0", json=body).status_code == 200
    job["status"] = "failed"
    store.save_job(job)
    assert client.patch("/api/projects/busy/cues/c0", json=body).status_code == 200


def test_shared_batch_provenance_survives_edit_snapshot_and_migration(store):
    from onsei.translation_records import compact_snapshot, expand_snapshot

    snapshot = store.input_snapshot("t")
    result = store.new_translation_run(snapshot, "index-nativelong-9b")
    session = TranslationSession(store, snapshot, result)
    cues = session.cues("t")
    metadata = {"context_ids": ["c0", "c1", "c2"], "model": "index-nativelong-9b"}
    for cue in cues:
        cue.zh_auto, cue.translation_status = "好的", "translated"
        cue.provenance["translation"] = metadata
    session.save_generated_batch(cues)
    store.edit("c0", {"zh_override": "人工"})
    before = store.input_snapshot("t")
    assert expand_snapshot(compact_snapshot(before)) == before
    with store.db(write=True) as db:
        assert db.execute("SELECT count(*) FROM translation_batches").fetchone()[0] == 1
        rows = db.execute("SELECT cue_id,payload FROM translation_cues").fetchall()
        for cue_id, raw in rows:
            row = json.loads(raw)
            assert row.pop("batch_ref") and "translation" not in row
            row["translation"] = metadata
            db.execute("UPDATE translation_cues SET payload=? WHERE cue_id=?", (json.dumps(row), cue_id))
        db.execute("DROP TABLE translation_batches")
        db.execute("PRAGMA user_version=4")
    assert Store(store.path).input_snapshot("t") == before
    store.delete_translation("t", result)
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM translation_batches").fetchone()[0] == 0
