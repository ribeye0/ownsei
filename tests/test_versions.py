import json
from onsei.util import file_hash
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

import onsei.api as service
from onsei.domain import Cue, Track
from onsei.export import export_track
from onsei.snapshots import fresh_exports
from onsei.storage import Store


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "p", "版本驗證")
    source = tmp_path / "audio.wav"
    source.write_bytes(b"untouched source")
    t = Track(
        id="t",
        name="track",
        source=str(source),
        sha256=file_hash(source),
        duration_ms=60000,
        sample_rate=16000,
        channels=1,
    )
    s.save_track(t)
    s.replace_cues(
        t,
        "whisper",
        [
            Cue(
                id=f"c{i}",
                track_id="t",
                version="whisper",
                ordinal=i,
                start_ms=i * 1000,
                end_ms=i * 1000 + 900,
                ja_raw=f"台詞{i}。",
            )
            for i in range(50)
        ],
    )
    return s


def populate(s, model, text):
    result = s.prepare_translation("t", model)
    for c in s.cues("t"):
        c.zh_auto = text
        c.translation_status = "translated"
        c.provenance["translation"] = {"model": model, "cache_key": model}
        assert s.save_generated(c, c.revision)
    return result


def test_independent_transcripts_models_manual_edits_and_exports(store):
    index = populate(store, "index-translate-9b", "Index譯文")
    store.edit("c0", {"zh_override": "Index人工校對"})
    old = store.cue("c0")
    index_files = export_track(store, "t", store.path / "exports")
    qwen = populate(store, "asmr-qwen3.5-9b", "Qwen譯文")
    assert not store.save_generated(old, old.revision)
    assert store.cue("c0").zh == "Qwen譯文"
    store.edit("c0", {"zh_override": "Qwen人工校對"})
    qwen_files = export_track(store, "t", store.path / "exports")
    assert set(index_files).isdisjoint(qwen_files)
    store.activate_translation("t", index)
    assert store.cue("c0").zh == "Index人工校對"
    assert set(fresh_exports(store)) == {p.name for p in index_files}
    store.activate_translation("t", qwen)
    assert store.cue("c0").zh == "Qwen人工校對"
    assert set(fresh_exports(store)) == {p.name for p in qwen_files}
    assert Store(store.path).cue("c0").zh == "Qwen人工校對"
    store.replace_cues(
        store.track("t"),
        "cohere",
        [Cue(id="new", track_id="t", version="cohere", ordinal=0, start_ms=0, end_ms=900, ja_raw="別の稿。")],
    )
    assert store.translation_versions("t") == []
    with pytest.raises(ValueError):
        store.activate_translation("t", index)
    track = store.track("t")
    track.active_version = "whisper"
    store.save_track(track)
    assert store.cue("c0").zh == "Qwen人工校對"


def test_existing_chinese_results_cleaned_in_api_and_all_exports(store, tmp_path, monkeypatch):
    index = populate(store, "index-translate-9b", "知道了，還沒說完。")
    qwen = populate(store, "asmr-qwen3.5-9b", "「等等，」\n真的？")
    monkeypatch.setattr(service, "REGISTRY", tmp_path / "index.db")
    client = TestClient(service.app, base_url="http://127.0.0.1")
    project = client.post("/api/projects", json={"title": "格式驗證", "path": str(store.path)}).json()["id"]
    for result, raw, rendered in (
        (index, "知道了，還沒說完。", "知道了，還沒說完"),
        (qwen, "「等等，」\n真的？", "「等等」\n真的？"),
    ):
        store.activate_translation("t", result)
        c = client.get(f"/api/projects/{project}/tracks/t/cues").json()[0]
        assert c["zh"] == rendered and c["zh_auto"] == raw
        files = export_track(store, "t", store.path / "exports")
        for p in files:
            text = p.read_text()
            if p.suffix == ".jsonl":
                exported = json.loads(text.splitlines()[0])
                assert exported["zh"] == rendered and exported["zh_auto"] == raw
            elif p.name.endswith((".zh-TW.txt", ".md")):
                assert rendered in text and raw not in text
            elif p.name.endswith((".zh-TW.srt", ".bilingual.srt", ".ass")):
                assert rendered.replace("\n", "\\N" if p.suffix == ".ass" else "\n") in text


def test_source_context_invalidates_all_models_and_preserves_manual(store):
    index = populate(store, "index-translate-9b", "Index譯文")
    store.edit("c0", {"zh_override": "人工"})
    qwen = populate(store, "asmr-qwen3.5-9b", "Qwen譯文")
    store.edit("c23", {"ja_override": "修正"})
    assert store.cue("c0").translation_status == "stale"
    assert store.cue("c24").translation_status == "stale"
    assert store.cue("c48").translation_status == "translated"
    store.activate_translation("t", index)
    assert store.cue("c0").zh == "人工" and store.cue("c0").translation_status == "manual"
    assert store.cue("c24").translation_status == "stale"
    store.update_settings({"notes": "新的角色設定"})
    store.activate_translation("t", qwen)
    assert store.cue("c48").translation_status == "stale"


def test_delete_only_one_result_archives_manual_and_changes_cache_generation(store):
    index = populate(store, "index-translate-9b", "Index譯文")
    store.edit("c0", {"zh_override": "人工"})
    qwen = populate(store, "asmr-qwen3.5-9b", "Qwen譯文")
    assert store.translation_nonce("t", "index-translate-9b") == 0
    store.delete_translation("t", index)
    assert [v["id"] for v in store.translation_versions("t")] == [qwen]
    assert store.cue("c0").zh == "Qwen譯文"
    assert store.translation_nonce("t", "index-translate-9b") != 0
    archived = next((store.path / "trash").glob("*/translation.json"))
    assert "人工" in archived.read_text()
    store.prepare_translation("t", "index-translate-9b")
    assert store.cue("c0").zh is None and store.cue("c0").ja_raw == "台詞0。"
    assert len(store.cues("t")) == 50


def test_delete_transcript_archives_checkpoints_and_preserves_audio_and_other_asr(store):
    populate(store, "index-translate-9b", "譯文")
    directory = store.path / "cache/t/raw-whisper"
    directory.mkdir(parents=True)
    (directory / "asr-00000.json").write_text("checkpoint")
    c = store.cue("c0")
    c.provenance["chunk"] = {"output": str(directory / "asr-00000.json")}
    store.save_generated(c, c.revision)
    store.replace_cues(
        store.track("t"),
        "cohere",
        [Cue(id="new", track_id="t", version="cohere", ordinal=0, start_ms=0, end_ms=900, ja_raw="別の稿。")],
    )
    store.delete_transcript("t", "whisper")
    assert store.track("t").active_version == "cohere" and store.cues("t")[0].id == "new"
    assert not directory.exists()
    assert next((store.path / "trash").glob("*/raw-whisper/asr-00000.json")).read_text() == "checkpoint"
    assert next((store.path / "trash").glob("*/transcript.jsonl")).exists()
    assert open(store.track("t").source, "rb").read() == b"untouched source"
    store.delete_transcript("t", "cohere")
    assert store.track("t").active_version is None and store.cues("t") == []


def test_legacy_migration_backs_up_raw_and_preserves_override(store):
    with store.db() as db:
        c = Cue.model_validate_json(db.execute('select payload from cues where id="c0"').fetchone()[0])
        c.zh_auto = "機器"
        c.zh_override = "人工"
        c.translation_status = "manual"
        c.provenance["translation"] = {"model": "index-translate-9b"}
        db.execute('update cues set payload=? where id="c0"', (c.model_dump_json(),))
        db.executescript(
            "DROP TABLE translation_cues; DROP TABLE translation_versions; DROP TABLE active_translations; PRAGMA user_version=1;"
        )
    migrated = Store(store.path)
    assert migrated.cue("c0").zh == "人工" and migrated.cue("c0").ja_raw == "台詞0。"
    assert migrated.translation_versions("t")[0]["model"] == "index-translate-9b"
    with sqlite3.connect(store.path / "project-before-versions.db") as db:
        assert (
            json.loads(db.execute('select payload from cues where id="c0"').fetchone()[0])["zh_override"]
            == "人工"
        )


def test_version_api_protects_running_jobs_and_scope(store, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "REGISTRY", tmp_path / "index.db")
    client = TestClient(service.app, base_url="http://127.0.0.1")
    p = client.post("/api/projects", json={"title": "版本驗證", "path": str(store.path)}).json()["id"]
    index = populate(store, "index-translate-9b", "譯文")
    base = f"/api/projects/{p}/tracks/t"
    job = store.new_job("translate", {"translation": "index-translate-9b"})
    assert job["options"]["transcript_versions"] == {"t": "whisper"}
    job.update(status="running", pid=os.getpid())
    store.save_job(job)
    for _ in range(51):
        done = store.new_job("translate", {})
        done["status"] = "completed"
        store.save_job(done)
    for path, method in [
        (f"/translations/{index}", "DELETE"),
        ("/versions/whisper", "DELETE"),
        ("/versions/whisper", "POST"),
        (f"/translations/{index}", "POST"),
    ]:
        assert client.request(method, base + path).status_code == 409
    job["status"] = "completed"
    store.save_job(job)
    assert client.get(base + "/translations").json()[0]["active"]
    assert client.delete(base + f"/translations/{index}").status_code == 200
    assert client.get(base + "/cues").json()[0]["zh"] is None
    assert client.delete(base + "/versions/whisper").status_code == 200
    assert store.track("t").active_version is None


@pytest.mark.parametrize(
    ("action", "model", "extra", "aligned"),
    [
        ("run", "whisper-ja-760m", False, False),
        ("run", "whisper-ja-760m", True, True),
        ("transcribe", "whisper-ja-760m", True, True),
        ("run", "whisper-ja-anime-v0.3", True, True),
        ("transcribe", "whisper-ja-anime-v0.3", False, False),
        ("transcribe", "whisper-ja-1.5b", True, True),
        ("run", "whisper-ja-1.5b", False, False),
        ("run", "cohere-asr-ja", True, False),
    ],
)
def test_alignment_switch_only_adds_full_whisper_alignment(
    store, tmp_path, monkeypatch, action, model, extra, aligned
):
    import onsei.runner as runner
    import onsei.asr as asr
    import onsei.translation_pipeline as pipeline
    import onsei.export as exports

    calls = []
    monkeypatch.setattr(runner, "STATE", tmp_path / "locks")
    monkeypatch.setattr(asr, "transcribe", lambda *args: calls.append(("asr", args[2])))
    monkeypatch.setattr(asr, "realign", lambda *args: calls.append(("align", args[2])))
    monkeypatch.setattr(pipeline, "process_translation", lambda *args, **kwargs: None)
    monkeypatch.setattr(exports, "export_track", lambda *args, **kwargs: None)
    job = store.new_job(action, {"asr": model, "extra_alignment": extra})
    runner.run_job(store, job["id"])
    assert store.job(job["id"])["status"] == "completed"
    assert any(c[0] == "align" for c in calls) == aligned
    if aligned:
        assert calls[1][1] == [c.id for c in store.cues("t")]
        assert len(store.cues("t", "whisper")) == 50
        assert store.cues("t")[0].provenance["extra_alignment"]


def test_uncategorized_legacy_manual_is_retained_with_known_machine_model(store):
    store.edit("c0", {"zh_override": "既有人工譯文"})
    second = store.cue("c1")
    second.zh_auto = "既有Index譯文"
    second.translation_status = "translated"
    second.provenance["translation"] = {"model": "index-translate-9b"}
    store.save_generated(second, second.revision)
    versions = store.translation_versions("t")
    assert len(versions) == 1 and versions[0]["model"] == "index-translate-9b"
    assert store.cue("c0").zh == "既有人工譯文"
    assert store.cue("c1").zh == "既有Index譯文"


def test_result_creation_order_is_stable_and_not_hash_order(store, monkeypatch):
    import onsei.storage as storage

    born = store.transcript_versions("t")[0]["created_at"]
    monkeypatch.setattr(storage.time, "time", lambda: born + 10)
    native = store.cues("t")[0].model_copy(deep=True)
    native.id, native.version = "a-cue", "a-new-version"
    store.replace_cues(store.track("t"), native.version, [native])
    newest = store.transcript_versions("t")[0]
    assert newest["version"] == "a-new-version" and newest["created_at"] == born + 10
    monkeypatch.setattr(storage.time, "time", lambda: born + 20)
    store.replace_cues(store.track("t"), native.version, [native])
    assert store.transcript_versions("t")[0]["created_at"] == born + 10
    index = store.prepare_translation("t", "index-translate-9b")
    monkeypatch.setattr(storage.time, "time", lambda: born + 30)
    qwen = store.prepare_translation("t", "asmr-qwen3.5-9b")
    assert [r["id"] for r in store.translation_versions("t")] == [qwen, index]


def test_legacy_transcript_times_are_marked_estimated_and_order_preserved(store):
    cs = store.cues("t")
    newer = cs[0].model_copy(deep=True)
    newer.id, newer.version = "newer", "a-sorts-before-whisper"
    store.replace_cues(store.track("t"), newer.version, [newer])
    with store.db() as db:
        db.executescript("DROP TABLE transcript_versions; PRAGMA user_version=2;")
    reopened = Store(store.path)
    versions = reopened.transcript_versions("t")
    assert [v["version"] for v in versions] == ["a-sorts-before-whisper", "whisper"]
    assert all(v["created_at"] is None and v["created_at_estimated"] for v in versions)
    assert len(reopened.cues("t", "whisper")) == 50


@pytest.mark.parametrize("model", ["whisper-ja-760m", "whisper-ja-anime-v0.3", "whisper-ja-1.5b"])
def test_deleted_asr_really_reruns_and_gets_new_ids(store, tmp_path, monkeypatch, model):
    import onsei.asr as module
    from pathlib import Path

    from onsei.util import file_hash

    track = store.track("t")
    track.sha256 = file_hash(Path(track.source))
    store.save_track(track)
    writes = []
    monkeypatch.setattr(module, "pcm", lambda *args: tmp_path / "audio.wav")
    part = {"index": 0, "start_ms": 0, "end_ms": 2000, "owned_start_ms": 0, "owned_end_ms": 2000}
    monkeypatch.setattr(module, "chunks", lambda *args: [dict(part)])

    def invoke(kind, model, audio, items, directory, progress):
        for item in items:
            p = Path(item["output"])
            if not p.exists():
                writes.append(p)
                p.write_text(
                    json.dumps(
                        {
                            "data": [
                                {"avg_logprob": -0.1, "words": [{"text": "はい。", "start": 0.1, "end": 0.9}]}
                            ]
                        }
                    )
                )

    monkeypatch.setattr(module, "invoke", invoke)
    first = module.transcribe(store, "t", model, None, lambda _: None)
    module.transcribe(store, "t", model, None, lambda _: None)
    assert first[0].provenance["model"] == model
    assert (first[0].start_ms, first[0].end_ms) == (100, 900)
    assert len(writes) == 1
    store.delete_transcript("t", first[0].version)
    second = module.transcribe(store, "t", model, None, lambda _: None)
    assert len(writes) == 2 and first[0].id != second[0].id
    assert second[0].ja == "はい" and second[0].ja_raw == "はい。"


def test_deleted_translation_bypasses_previously_accepted_cache(store, monkeypatch):
    import onsei.translation as module

    calls = []

    class Engine:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def generate(self, data):
            calls.append(data)
            return json.dumps([{"id": row["id"], "text": f"結果{len(calls)}"} for row in data["lines"]])

    monkeypatch.setattr(module, "Llama", Engine)
    module.translate(store, "t", "index-translate-9b", None, lambda _: None)
    first = store.cue("c0").zh
    result = store.translation_versions("t")[0]["id"]
    store.delete_translation("t", result)
    module.translate(store, "t", "index-translate-9b", None, lambda _: None)
    assert len(calls) == 6 and store.cue("c0").zh != first


def test_combined_view_and_switch_reply_match_selected_ids_and_text(store, tmp_path, monkeypatch):
    from onsei.view import track_view

    index = populate(store, "index-translate-9b", "Index。")
    qwen = populate(store, "asmr-qwen3.5-9b", "Qwen？")
    monkeypatch.setattr(service, "REGISTRY", tmp_path / "index.db")
    client = TestClient(service.app, base_url="http://127.0.0.1")
    p = client.post("/api/projects", json={"title": "快照驗證", "path": str(store.path)}).json()["id"]
    base = f"/api/projects/{p}/tracks/t"
    for identity, text in [(index, "Index"), (qwen, "Qwen？"), (index, "Index")]:
        reply = client.post(base + f"/translations/{identity}").json()
        assert reply["ok"] and reply["track"]["active_version"] == "whisper"
        assert len(reply["cues"]) == 50 and reply["cues"][0]["zh"] == text
        assert reply["cues"][0]["provenance"]["translation_version"] == identity
        assert [v["id"] for v in reply["translations"] if v["active"]] == [identity]
        assert client.get(base + "/view").json() == track_view(store, "t")
    assert client.post(base + "/versions/whisper").json()["cues"][0]["zh"] == "Index"
    assert client.get(f"/api/projects/{p}/jobs").json() == store.jobs()


def test_view_reads_exports_from_same_snapshot_during_concurrent_change(store, monkeypatch):
    import onsei.view as views
    import onsei.snapshots as snapshots

    populate(store, "index-translate-9b", "譯文")
    files = export_track(store, "t", store.path / "exports")
    original = snapshots.export_key

    def change_during_snapshot(track_id, cues):
        key = original(track_id, cues)
        with store.db() as writer:
            writer.execute(
                "UPDATE settings SET payload=? WHERE key=?",
                (json.dumps({"digest": "changed", "files": []}), key),
            )
        return key

    monkeypatch.setattr(snapshots, "export_key", change_during_snapshot)
    view = views.track_view(store, "t")
    assert set(view["files"]) == {p.name for p in files}
    assert fresh_exports(store) == []
