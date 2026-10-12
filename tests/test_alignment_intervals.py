import json
from pathlib import Path

import pytest

from onsei.asr import aligned_sentences, alignment_times, normalize
from onsei.domain import Track
from onsei.storage import Store


def test_zero_duration_sentences_retain_text_with_adjacent_supported_times():
    # Regression: Qwen emits a 22.48 -> 22.48 tail sentence at the window edge.
    spans = aligned_sentences(
        "はい。あっ。",
        [
            {"text": "はい", "start": 18.96, "end": 22.48},
            {"text": "あっ", "start": 22.48, "end": 22.48},
        ],
        24000,
        24500,
    )
    assert len(spans) == 1
    assert spans[0]["text"] == "はい。あっ。"
    assert (spans[0]["start_ms"], spans[0]["end_ms"]) == (42960, 46480)
    assert set(spans[0]["review_flags"]) == {"alignment_zero_duration", "alignment_merged"}

    text = "あっ。はい。えっ。いいえ。"
    spans = aligned_sentences(
        text,
        [
            {"text": "あっ", "start": 1, "end": 1},
            {"text": "はい", "start": 1, "end": 2},
            {"text": "えっ", "start": 2, "end": 2},
            {"text": "いいえ", "start": 3, "end": 4},
        ],
        0,
        4000,
    )
    assert [s["text"] for s in spans] == ["あっ。はい。えっ。", "いいえ。"]
    assert [(s["start_ms"], s["end_ms"]) for s in spans] == [(1000, 2000), (3000, 4000)]
    assert normalize("".join(s["text"] for s in spans)) == normalize(text)


def test_submillisecond_points_and_completely_unsupported_text():
    spans = aligned_sentences(
        "はい。あっ。",
        [
            {"text": "はい", "start": 1, "end": 2},
            {"text": "あっ", "start": 2, "end": 2.0001},
        ],
        0,
    )
    assert len(spans) == 1 and spans[0]["end_ms"] == 2000
    with pytest.raises(ValueError, match="no positive-duration"):
        aligned_sentences("あっ。", [{"text": "あっ", "start": 1, "end": 1}], 0)


@pytest.mark.parametrize("start,end", [(-1, 1), (2, 1), (float("nan"), 1), (0, float("inf")), (0, 5)])
def test_corrupt_token_times_remain_errors(start, end):
    with pytest.raises(ValueError, match="invalid token times"):
        alignment_times("はい", [{"text": "はい", "start": start, "end": end}], 4000)


@pytest.mark.parametrize("duration,end", [(2420, 2.48), (3490, 3.52), (2820, 2.88), (5100, 5.12)])
def test_quantized_qwen_edge_clipped_to_actual_slice(duration, end):
    times = alignment_times("はい", [{"text": "はい", "start": 1, "end": end}], duration)
    assert times[-1][1] == duration / 1000
    with pytest.raises(ValueError, match="invalid token times"):
        alignment_times("はい", [{"text": "はい", "start": 1, "end": duration / 1000 + 0.081}], duration)


@pytest.mark.parametrize(
    "data", [[], [{"text": "はぁ", "start": -1, "end": 1}], [{"text": "別の文字", "start": 0, "end": 1}]]
)
def test_invalid_or_empty_optional_alignment_retains_verified_native(tmp_path, monkeypatch, data):
    import onsei.asr as module

    s = retry_project(tmp_path)
    c = s.cues("t")[0]
    monkeypatch.setattr(module, "pcm", lambda *_: tmp_path / "fake.wav")

    def invoke(_kind, _model, _audio, items, _directory, _progress):
        for item in items:
            Path(item["output"]).write_text(json.dumps({"data": data}))

    monkeypatch.setattr(module, "invoke", invoke)
    module.realign(s, "t", [c.id], lambda _: None)
    after = s.cues("t")[0]
    assert (after.start_ms, after.end_ms, after.timing_status) == (3000, 3600, "asr")
    assert "alignment_native_fallback" in after.review_flags
    assert after.provenance["alignment_diagnostic"]["reason"]


def test_cohere_resume_reuses_checkpoints_retains_previous_version_and_flags_cue(tmp_path, monkeypatch):
    import onsei.asr as module
    from onsei.domain import Cue

    store = Store.create(tmp_path / "project", "test")
    track = Track(
        id="t",
        name="track",
        source="fake.wav",
        sha256="hash",
        duration_ms=24500,
        sample_rate=16000,
        channels=1,
    )
    store.save_track(track)
    old = Cue(
        id="old",
        track_id="t",
        version="old",
        ordinal=0,
        start_ms=100,
        end_ms=500,
        ja_raw="旧稿",
        zh_override="人工譯文",
        translation_status="manual",
    )
    store.replace_cues(track, "old", [old])
    part = {"index": 0, "start_ms": 0, "end_ms": 24500, "owned_start_ms": 0, "owned_end_ms": 24500}
    monkeypatch.setattr(module, "pcm", lambda *_: tmp_path / "audio.wav")
    monkeypatch.setattr(module, "chunks", lambda *_: [dict(part)])
    writes = []

    def checkpoints(kind, _model, _audio, items, _directory, _progress):
        for item in items:
            path = Path(item["output"])
            if path.exists():
                continue
            writes.append(kind)
            data = (
                {"text": "はい。あっ。"}
                if kind == "cohere"
                else [
                    {"text": "はい", "start": 18.96, "end": 22.48},
                    {"text": "あっ", "start": 22.48, "end": 22.48},
                ]
            )
            path.write_text(json.dumps({"data": data}))

    monkeypatch.setattr(module, "invoke", checkpoints)
    cues = module.transcribe(store, "t", "cohere-asr-ja", None, lambda _: None)
    assert writes == ["cohere", "aligner"]
    assert len(cues) == 1 and cues[0].ja_raw == "はい。あっ。" and cues[0].ja == "はい。あっ"
    assert "alignment_merged" in cues[0].review_flags
    assert cues[0].timing_status == "aligned" and cues[0].translation_status == "pending"
    assert store.cues("t", "old")[0].zh == "人工譯文"
    with store.db() as db:
        db.execute("DELETE FROM cues WHERE version=?", (cues[0].version,))
    writes.clear()
    resumed = module.transcribe(store, "t", "cohere-asr-ja", None, lambda _: None)
    assert not writes and resumed[0].id == cues[0].id


def retry_project(tmp_path, model="whisper-ja-760m"):
    from onsei.domain import Cue

    s = Store.create(tmp_path / "retry", "test")
    t = Track(
        id="t",
        name="test",
        source="fake.wav",
        sha256="hash",
        duration_ms=10000,
        sample_rate=16000,
        channels=1,
    )
    s.save_track(t)
    c = Cue(
        id="native",
        track_id="t",
        version="native",
        ordinal=0,
        start_ms=3000,
        end_ms=3600,
        ja_raw="はぁ",
        provenance={"model": model, "chunk": {"index": 0, "start_ms": 0, "end_ms": 10000}},
    )
    s.replace_cues(t, "native", [c])
    from onsei.asr import alignment_copy

    alignment_copy(s, "t")
    return s


def test_point_cue_retries_local_audio_preserving_native_and_text(tmp_path, monkeypatch):
    import onsei.asr as module

    s = retry_project(tmp_path)
    original = s.cues("t")[0]
    calls = []
    monkeypatch.setattr(module, "pcm", lambda *_: tmp_path / "fake.wav")

    def invoke(_kind, _model, _audio, items, _directory, _progress):
        for item in items:
            calls.append(item)
            local = "retry-" in Path(item["output"]).name
            Path(item["output"]).write_text(
                json.dumps(
                    {"data": [{"text": "はぁ", "start": 0.6 if local else 8, "end": 1.1 if local else 8}]}
                )
            )

    monkeypatch.setattr(module, "invoke", invoke)
    module.realign(s, "t", [original.id], lambda _: None)
    after = s.cues("t")[0]
    assert len(calls) == 2 and (calls[1]["start_ms"], calls[1]["end_ms"]) == (2500, 4100)
    assert (after.start_ms, after.end_ms, after.timing_status) == (3100, 3600, "aligned")
    assert after.id == original.id and after.ja == original.ja
    assert after.provenance["alignment_diagnostic"]["method"] == "local_retry"
    native = s.cues("t", "native")[0]
    assert (native.start_ms, native.end_ms, native.timing_status) == (3000, 3600, "asr")


@pytest.mark.parametrize("edited", [False, True])
@pytest.mark.parametrize("model", ["whisper-ja-760m", "whisper-ja-anime-v0.3", "whisper-ja-1.5b"])
def test_persistent_points_fallback_only_to_unchanged_native(tmp_path, monkeypatch, edited, model):
    import onsei.asr as module

    s = retry_project(tmp_path, model)
    c = s.cues("t")[0]
    if edited:
        s.edit(c.id, {"ja_override": "えっ"}, c.revision)
    monkeypatch.setattr(module, "pcm", lambda *_: tmp_path / "fake.wav")

    def invoke(_kind, _model, _audio, items, _directory, _progress):
        for item in items:
            Path(item["output"]).write_text(
                json.dumps({"data": [{"text": item["text"], "start": 1, "end": 1}]})
            )

    monkeypatch.setattr(module, "invoke", invoke)
    if edited:
        with pytest.raises(RuntimeError, match="Alignment failed"):
            module.realign(s, "t", [c.id], lambda _: None)
        assert s.cues("t")[0].timing_status == "alignment_failed"
    else:
        module.realign(s, "t", [c.id], lambda _: None)
        after = s.cues("t")[0]
        assert after.timing_status == "asr" and "alignment_native_fallback" in after.review_flags
        assert (after.start_ms, after.end_ms) == (3000, 3600)
        assert after.provenance["alignment_diagnostic"]["method"] == "native_fallback"
        native = s.track("t")
        native.active_version = "native"
        s.save_track(native)
        assert not module.alignment_copy(s, "t")


def test_positive_but_wrong_alignment_retries_then_retains_native(tmp_path, monkeypatch):
    import onsei.asr as module

    s = retry_project(tmp_path)
    c = s.cues("t")[0]
    monkeypatch.setattr(module, "pcm", lambda *_: tmp_path / "fake.wav")
    calls = []

    def invoke(_kind, _model, _audio, items, _directory, _progress):
        for item in items:
            calls.append(item)
            Path(item["output"]).write_text(
                json.dumps({"data": [{"text": item["text"], "start": 0, "end": 0.2}]})
            )

    monkeypatch.setattr(module, "invoke", invoke)
    module.realign(s, "t", [c.id], lambda _: None)
    after = s.cues("t")[0]
    assert len(calls) == 2
    assert (calls[0]["start_ms"], calls[0]["end_ms"]) == (2500, 4100)
    assert (after.start_ms, after.end_ms, after.timing_status) == (3000, 3600, "asr")
    assert "alignment_native_fallback" in after.review_flags
    assert "Native timing guard" in after.provenance["alignment_diagnostic"]["reason"]


def test_small_supported_timing_improvement_is_accepted(tmp_path, monkeypatch):
    import onsei.asr as module

    s = retry_project(tmp_path)
    monkeypatch.setattr(module, "pcm", lambda *_: tmp_path / "fake.wav")
    calls = []

    def invoke(_kind, _model, _audio, items, _directory, _progress):
        for item in items:
            calls.append(item)
            Path(item["output"]).write_text(
                json.dumps({"data": [{"text": item["text"], "start": 0.55, "end": 1.05}]})
            )

    monkeypatch.setattr(module, "invoke", invoke)
    module.realign(s, "t", [s.cues("t")[0].id], lambda _: None)
    after = s.cues("t")[0]
    assert len(calls) == 1
    assert (after.start_ms, after.end_ms, after.timing_status) == (3050, 3550, "aligned")
