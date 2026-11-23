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
