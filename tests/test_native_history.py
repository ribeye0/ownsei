import json

import pytest

from onsei.domain import Cue, Track
from onsei.storage import Store
from onsei.translation import translate
from onsei.context import DOCUMENT_CONFIG, NATIVE_LONG, translation_options, previous_translations


def project(tmp_path, monkeypatch):
    monkeypatch.setitem(DOCUMENT_CONFIG, "batch_size", 2)
    store = Store.create(tmp_path / "p", "history")
    store.update_settings({"translation_options": {"context": 16384, "names": False, "history": True}})
    track = Track(
        id="t", name="test", source="/fake", sha256="h", duration_ms=6000, sample_rate=16000, channels=1
    )
    store.save_track(track)
    store.replace_cues(
        track,
        "v",
        [
            Cue(
                id=f"c{i}",
                track_id="t",
                version="v",
                ordinal=i,
                start_ms=i * 1000,
                end_ms=i * 1000 + 900,
                ja_raw=f"こんにちは{i}",
            )
            for i in range(6)
        ],
    )
    return store


class Engine:
    def __init__(self, split=False):
        self.calls, self.split = [], split

    def generate(self, data):
        self.calls.append(data)
        if self.split:
            self.split = False
            raise ValueError("Translation output incomplete/truncated")
        return json.dumps([{"id": r["id"], "text": "你好" + r["id"]} for r in data["lines"]])


def test_only_contiguous_accepted_past_lines_are_used():
    cues = [
        Cue(
            id=f"c{i}",
            track_id="t",
            version="v",
            ordinal=i,
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            ja_raw=f"原文{i}",
        )
        for i in range(6)
    ]
    accepted = {"c0": "早先譯文", "c2": "已保存", "c3": "已保存", "c5": "未來台詞"}
    rows = previous_translations(cues, [cues[4]], accepted)
    assert [r["id"] for r in rows] == ["3", "4"]
    assert previous_translations(cues, [cues[4]], accepted, limit=1) == []
    assert previous_translations(cues, [cues[0]], accepted) == []


def test_split_retry_uses_saved_child_results_and_history_is_part_of_cache(tmp_path, monkeypatch):
    store = project(tmp_path, monkeypatch)
    engine = Engine(split=True)
    translate(store, "t", NATIVE_LONG, None, lambda _: None, engine=engine)
    assert [r["id"] for r in engine.calls[2]["document_history"]] == ["1"]
    assert [r["id"] for r in engine.calls[3]["document_history"]] == ["1", "2"]
    saved = store.cues("t")
    assert all(c.translation_status == "translated" for c in saved)
    assert saved[2].provenance["translation"]["document_history"][0]["zh"] == "你好c0"
    inputs = [json.loads(p.read_text()).get("input", {}) for p in (store.path / "cache").rglob("*.json")]
    assert any(r.get("document_history") for r in inputs)


def test_unsaved_prediction_never_becomes_history(tmp_path, monkeypatch):
    store = project(tmp_path, monkeypatch)
    save = store.save_generated
    monkeypatch.setattr(store, "save_generated", lambda c, rev: False if c.id == "c0" else save(c, rev))
    engine = Engine()
    translate(store, "t", NATIVE_LONG, None, lambda _: None, engine=engine)
    assert [r["id"] for r in engine.calls[1]["document_history"]] == ["2"]
    assert all(r["id"] != "1" for call in engine.calls for r in call["document_history"])


def test_saved_dependencies_only_include_history_used_by_engine(tmp_path, monkeypatch):
    store = project(tmp_path, monkeypatch)

    class TrimmedEngine(Engine):
        def execution_metadata(self):
            history = self.calls[-1].get("document_history", [])
            return {"document_context": {"history_ids": [r["id"] for r in history[-1:]]}}

    translate(store, "t", NATIVE_LONG, None, lambda _: None, engine=TrimmedEngine())
    assert [r["id"] for r in store.cue("c2").provenance["translation"]["document_history"]] == ["2"]
    assert [r["id"] for r in store.cue("c4").provenance["translation"]["document_history"]] == ["4"]


def test_chinese_edit_invalidates_dependent_batches_transitively(tmp_path, monkeypatch):
    store = project(tmp_path, monkeypatch)
    translate(store, "t", NATIVE_LONG, None, lambda _: None, engine=Engine())
    cue = store.cue("c0")
    store.edit(cue.id, {"zh_override": "人工修正"}, cue.revision)
    result = store.cues("t")
    assert result[0].zh == "人工修正" and result[0].translation_status == "manual"
    assert result[1].translation_status == "translated"
    assert all(c.translation_status == "stale" for c in result[2:])


def test_queued_translation_options_are_pinned_without_changing_project(tmp_path, monkeypatch):
    store = project(tmp_path, monkeypatch)
    options = {"context": 65536, "names": True, "history": False}
    job = store.new_job("translate", {"translation": NATIVE_LONG, "translation_options": options})
    options["context"] = 16384
    snapshot = job["options"]["input_snapshots"]["t"]
    assert snapshot["settings"]["translation_options"]["context"] == 65536
    assert store.settings()["translation_options"]["context"] == 16384


def test_changed_context_cannot_resume_or_mutate_a_completed_result(tmp_path, monkeypatch):
    from onsei.translation_pipeline import process_translation

    store = project(tmp_path, monkeypatch)

    class ManagedEngine(Engine):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def record(self, *args, **kwargs):
            pass

        def execution_metadata(self):
            return {}

    monkeypatch.setattr("onsei.translation_pipeline.Llama", ManagedEngine)
    options = {"context": 32768, "names": False, "history": True}
    result = process_translation(store, "t", NATIVE_LONG, lambda _: None, translation_options=options)
    before = store.run_metadata(result["draft"])
    with pytest.raises(ValueError, match="設定或 prompt 已更新"):
        process_translation(
            store,
            "t",
            NATIVE_LONG,
            lambda _: None,
            outputs=result,
            translation_options={**options, "context": 65536},
        )
    assert store.run_metadata(result["draft"]) == before


def test_edit_during_generation_is_read_only_and_next_batch_uses_generated_history(tmp_path, monkeypatch):
    from onsei.translation_runs import TranslationSession

    store = project(tmp_path, monkeypatch)
    snapshot = store.input_snapshot("t")
    identity = store.new_translation_run(snapshot, NATIVE_LONG)
    session = TranslationSession(store, snapshot, identity)

    from onsei.storage import ConflictError

    store.new_job("translate", {"track_ids": ["t"]})

    class EditingEngine(Engine):
        def generate(self, data):
            if self.calls:
                assert data["document_history"]
                cue = store.cue("c0")
                with pytest.raises(ConflictError, match="字幕暫時唯讀"):
                    store.edit(cue.id, {"zh_override": "最新人工譯文"}, cue.revision)
            return super().generate(data)

    translate(session, "t", NATIVE_LONG, None, lambda _: None, engine=EditingEngine())
    assert all(c.translation_status == "translated" for c in session.cues("t"))


@pytest.mark.parametrize(
    "bad", [{"context": 1}, {"context": True}, {"names": "false"}, {"history": 1}, {"unexpected": True}]
)
def test_translation_options_reject_invalid_values(bad):
    with pytest.raises(ValueError, match="translation settings"):
        translation_options({"translation_options": bad})


@pytest.mark.parametrize("suffix", ["，", "。", ",", ".", "、", "，」"])
def test_session_history_compares_rendered_machine_subtitles(tmp_path, monkeypatch, suffix):
    from onsei.translation_runs import TranslationSession
    from onsei.domain import subtitle_zh

    store = project(tmp_path, monkeypatch)
    snapshot = store.input_snapshot("t")
    identity = store.new_translation_run(snapshot, NATIVE_LONG)
    session = TranslationSession(store, snapshot, identity)

    class PunctuatedEngine(Engine):
        def generate(self, data):
            self.calls.append(data)
            return json.dumps([{"id": r["id"], "text": "你好" + r["id"] + suffix} for r in data["lines"]])

    engine = PunctuatedEngine()
    translate(session, "t", NATIVE_LONG, None, lambda _: None, engine=engine)
    result = session.cues("t")
    assert all(c.translation_status == "translated" for c in result)
    assert result[0].zh_auto.endswith(suffix)  # Keep original model output.
    assert engine.calls[2]["document_history"][0]["zh"] == subtitle_zh(result[0].zh_auto)


def test_human_history_punctuation_is_not_normalized_away(tmp_path, monkeypatch):
    from onsei.translation_runs import TranslationSession

    store = project(tmp_path, monkeypatch)
    first = store.cue("c0")
    store.edit(first.id, {"zh_override": "人工句尾，"}, first.revision)
    snapshot = store.input_snapshot("t")
    identity = store.new_translation_run(snapshot, NATIVE_LONG)
    session = TranslationSession(store, snapshot, identity)

    engine = Engine()
    translate(session, "t", NATIVE_LONG, None, lambda _: None, engine=engine)
    assert engine.calls[1]["document_history"][0]["zh"] == "人工句尾，"
    assert session.cues("t")[0].zh == "人工句尾，"


def test_successful_resume_clears_previous_failure_metadata(tmp_path, monkeypatch):
    from onsei.translation_pipeline import process_translation

    store = project(tmp_path, monkeypatch)

    class ManagedEngine(Engine):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def record(self, *args, **kwargs):
            pass

        def execution_metadata(self):
            return {}

    monkeypatch.setattr("onsei.translation_pipeline.Llama", ManagedEngine)
    result = process_translation(store, "t", NATIVE_LONG, lambda _: None)
    store.update_run(result["draft"], status="partial", execution_status="failed", error="old failure")
    assert process_translation(store, "t", NATIVE_LONG, lambda _: None, outputs=result) == result
    metadata = store.run_metadata(result["draft"])
    assert metadata["status"] == metadata["execution_status"] == "completed"
    assert metadata["error"] is None
