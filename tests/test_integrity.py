import json
import pytest
from onsei.asr import merge_spans
from onsei.domain import Cue, Track
from onsei.export import export_track
from onsei.storage import Store
from onsei.translation import parse_reply, payload


@pytest.fixture
def project(tmp_path):
    s = Store.create(tmp_path / "project", "test")
    t = Track(
        id="t",
        name="音聲測試",
        source="/tmp/fake.wav",
        sha256="hash",
        duration_ms=60000,
        sample_rate=48000,
        channels=2,
    )
    s.save_track(t)
    cs = [
        Cue(
            id=f"c{i}",
            track_id="t",
            version="v1",
            ordinal=i,
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            ja_raw=f"台詞{i}",
            zh_auto=f"翻譯{i}",
            translation_status="translated",
        )
        for i in range(49)
    ]
    s.replace_cues(t, "v1", cs)
    return s


def test_overlap_retains_real_repetition():
    spans = [
        {"text": "はい。", "start_ms": 1000, "end_ms": 2000},
        {"text": "はい", "start_ms": 1100, "end_ms": 2100},
        {"text": "はい。", "start_ms": 2500, "end_ms": 3000},
    ]
    assert len(merge_spans(spans)) == 2


def test_edits_guard_inflight_and_invalidate_bounded_context(project):
    before = project.cue("c9")
    neighbor = project.cue("c10")
    project.edit("c9", {"ja_override": "修正"}, before.revision)
    assert project.cue("c0").translation_status == "stale"
    assert project.cue("c10").translation_status == "stale"
    assert project.cue("c24").translation_status == "translated"
    assert not project.save_generated(before, before.revision)
    assert not project.save_generated(neighbor, neighbor.revision)
    manual = project.edit("c5", {"zh_override": "人工譯文"})
    generated = manual.model_copy(update={"zh_auto": "機器新譯文"})
    assert project.save_generated(generated, manual.revision)
    assert project.cue("c5").zh == "人工譯文"
    c = project.edit("c30", {"start_ms": 30100})
    assert c.translation_status == "translated" and c.timing_status == "manual"
    with pytest.raises(ValueError):
        project.edit("c20", {"end_ms": 70000})


def test_settings_keep_manual(project):
    project.edit("c0", {"zh_override": "人工"})
    project.update_settings({"glossary": {"先輩": "學長"}})
    assert project.cue("c0").zh == "人工"
    assert project.cue("c1").translation_status == "stale"


def test_full_editor_payload_only_invalidates_changed_fields(project):
    c = project.cue("c0")
    changes = {
        "ja_override": c.ja_override,
        "zh_override": c.zh_override,
        "start_ms": c.start_ms,
        "end_ms": c.end_ms,
        "review_flags": ["listen"],
    }
    edited = project.edit(c.id, changes, c.revision)
    assert edited.translation_status == c.translation_status
    assert edited.timing_status == c.timing_status
    changes["start_ms"] = 100
    edited = project.edit(c.id, changes, edited.revision)
    assert edited.translation_status == "translated"
    assert edited.timing_status == "manual"
    changes["zh_override"] = "人工譯文"
    edited = project.edit(c.id, changes, edited.revision)
    assert edited.translation_status == "manual"


def test_translation_requires_exact_ids():
    assert parse_reply('[{"id":"a","text":"好"}]', ["a"]) == {"a": "好"}
    for text in (
        "[]",
        '[{"id":"a","text":""}]',
        '[{"id":"b","text":"好"}]',
        '[{"id":"a","text":"好"},{"id":"a","text":"好"}]',
        '{"a":"好"}',
    ):
        with pytest.raises((ValueError, TypeError)):
            parse_reply(text, ["a"])


def test_context_only_uses_bounded_japanese(project):
    cs = project.cues("t")
    p = payload(cs, cs[10:20], project.settings())
    assert p["before"] == [f"台詞{i}" for i in range(4, 10)]
    assert p["after"] == [f"台詞{i}" for i in range(20, 26)]
    assert "翻譯" not in json.dumps(p, ensure_ascii=False).split('"before"', 1)[1]


def test_exports_refuse_stale(project, tmp_path):
    paths = export_track(project, "t", tmp_path / "out")
    assert len(paths) == 16
    assert sum(p.suffix == ".ass" for p in paths) == 3
    assert sum(p.suffix == ".lrc" for p in paths) == 3
    assert not any(p.suffix == ".md" for p in paths)
    subs = next(p for p in paths if p.name.endswith("bilingual.srt")).read_text()
    assert "台詞0\n翻譯0" in subs
    project.edit("c0", {"ja_override": "修正日文"})
    with pytest.raises(ValueError):
        export_track(project, "t", tmp_path / "out")


def test_new_asr_version_retains_old(project):
    project.edit("c0", {"ja_override": "校對"})
    c = Cue(id="new", track_id="t", version="v2", ordinal=0, start_ms=0, end_ms=500, ja_raw="新辨識")
    project.replace_cues(project.track("t"), "v2", [c])
    assert project.cues("t")[0].id == "new" and project.cues("t", "v1")[0].ja == "校對"


def test_word_ownership_and_readable_cue_splits():
    from onsei.asr import whisper_spans

    part = {"start_ms": 23500, "owned_start_ms": 24000, "owned_end_ms": 48000}
    segment = {
        "text": "前。はい。次の台詞",
        "start": 0,
        "end": 10,
        "avg_logprob": -0.3,
        "words": [
            {"text": "前。", "start": 0, "end": 0.4},
            {"text": "はい。", "start": 0.7, "end": 1.5},
            {"text": "次の", "start": 2.5, "end": 3},
            {"text": "台詞", "start": 3, "end": 4},
        ],
    }
    spans = whisper_spans(segment, part)
    assert [s["text"] for s in spans] == ["はい。", "次の台詞"]
    assert spans[0]["start_ms"] == 24200


def test_exports_are_hidden_after_source_edits(project, tmp_path):
    from onsei.snapshots import fresh_exports

    paths = export_track(project, "t", tmp_path / "out")
    assert set(fresh_exports(project)) == {p.name for p in paths}
    project.edit("c0", {"zh_override": "校對後譯文"})
    assert fresh_exports(project) == []
    assert all(p.exists() for p in paths)


def test_japanese_character_tokens_split_at_punctuation_and_join_windows(tmp_path):
    from onsei.asr import whisper_spans, whisper_track_spans

    part = {"start_ms": 0, "owned_start_ms": 0, "owned_end_ms": 24000}
    words = [
        {"text": "吸い殻、空き缶……", "start": 0, "end": 6},
        {"text": "吸", "start": 6, "end": 8},
        {"text": "い殻。", "start": 8, "end": 9},
    ]
    segment = {"avg_logprob": -0.2, "words": words}
    assert [s["text"] for s in whisper_spans(segment, part)] == ["吸い殻、空き缶……", "吸い殻。"]
    parts = []
    for i, offset, a, b, token, start, end in [
        (0, 0, 0, 24000, "自", 23.8, 24.1),
        (1, 23500, 24000, 48000, "分です。", 0.6, 1.5),
    ]:
        output = tmp_path / f"{i}.json"
        output.write_text(
            json.dumps(
                {"data": [{"avg_logprob": -0.2, "words": [{"text": token, "start": start, "end": end}]}]}
            )
        )
        parts.append(
            {"index": i, "start_ms": offset, "owned_start_ms": a, "owned_end_ms": b, "output": str(output)}
        )
    spans = whisper_track_spans(parts)
    assert [s["text"] for s in spans] == ["自分です。"]
    assert (spans[0]["start_ms"], spans[0]["end_ms"]) == (23800, 25000)


def test_context_change_discards_inflight_document_batch(project, monkeypatch):
    from onsei.context import DOCUMENT_CONFIG

    monkeypatch.setitem(DOCUMENT_CONFIG, "batch_size", 24)
    import onsei.translation as module

    calls = []

    class Engine:
        def __init__(self, *_, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def generate(self, data):
            calls.append(data)
            if len(calls) == 1:
                project.edit("c23", {"ja_override": "推論期間修正的台詞"})
            return json.dumps([{"id": line["id"], "text": "舊推論"} for line in data["lines"]])

    monkeypatch.setattr(module, "Llama", Engine)
    module.translate(project, "t", "index-translate-9b", ["c23"], lambda _: None)
    assert len(calls) == 1
    assert project.cue("c0").translation_status == "stale"
    assert project.cue("c24").translation_status == "stale"
    assert project.cue("c47").translation_status == "stale"
    assert project.cue("c48").translation_status == "translated"
    assert project.cue("c24").zh == "翻譯24"
    assert project.cue("c23").ja == "推論期間修正的台詞"
