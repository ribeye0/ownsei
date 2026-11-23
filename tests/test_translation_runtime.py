import io
import json

import pytest

import onsei.translation as translation
from onsei import inference, models
from onsei.domain import Cue, Track
from onsei.storage import Store
from onsei.translation_runtime import inference_summary


def test_cuda_prefill_uses_measured_baseline_and_explicit_override(monkeypatch):
    import onsei.translation_runtime as runtime

    monkeypatch.delenv("ONSEI_PREFILL_MICROBATCH", raising=False)
    assert runtime.prefill_microbatch("qwen3.5-4b-names") == 512
    assert runtime.prefill_microbatch("index-translate-9b") == 512
    monkeypatch.setenv("ONSEI_PREFILL_MICROBATCH", "1024")
    assert runtime.prefill_microbatch("index-translate-9b") == 1024
    monkeypatch.setenv("ONSEI_PREFILL_MICROBATCH", "9000")
    with pytest.raises(ValueError, match="PREFILL_MICROBATCH"):
        runtime.prefill_microbatch("index-translate-9b")


def test_prefill_oom_retries_smaller_without_changing_decoding(monkeypatch, tmp_path):
    engine = translation.Llama("index-translate-9b", tmp_path)
    engine.prefill_microbatch = 1024
    original = engine.profile
    engine.log = io.StringIO()
    started = []
    monkeypatch.setattr(engine, "_stop_server", lambda: None)
    monkeypatch.setattr(engine, "_start_server", lambda: started.append(engine.prefill_microbatch))
    assert engine.fallback("CUDA out of memory")
    assert started == [512] and engine.profile == original


def test_performance_summary_includes_rejected_work_and_real_saved_throughput():
    rows = [
        {
            "validation": "accepted",
            "timings": {
                "prompt_ms": 100,
                "predicted_ms": 1000,
                "predicted_n": 100,
                "draft_n": 120,
                "draft_n_accepted": 60,
            },
        },
        {
            "validation": "rejected",
            "timings": {
                "prompt_ms": 200,
                "predicted_ms": 2000,
                "predicted_n": 200,
                "draft_n": 100,
                "draft_n_accepted": 50,
            },
        },
        {"seconds": 1, "validation": "rejected"},
    ]
    summary = inference_summary(rows, {"saved_lines": 12, "retries": 1, "splits": 1}, 10)
    assert summary["prefill_seconds"] == 0.3
    assert summary["decode_seconds"] == 3
    assert summary["decode_tokens_per_second"] == 100
    assert summary["draft_acceptance"] == 0.5
    assert summary["saved_lines_per_minute"] == 72
    assert summary["requests"] == 3 and summary["retries"] == summary["splits"] == 1
    assert inference_summary([], {}, 0)["decode_tokens_per_second"] is None


def test_bad_accelerated_output_switches_once_to_baseline(tmp_path, monkeypatch):
    engine = translation.Llama("index-translate-9b", tmp_path, speculative="draft-mtp")
    engine.log = io.StringIO()
    engine.last_request = {"lines": 24, "validation": "pending"}
    starts = []
    stops = []
    monkeypatch.setattr(engine, "_start_server", lambda: starts.append(engine.profile.speculative))
    monkeypatch.setattr(engine, "_stop_server", lambda: stops.append(True))
    assert engine.reject(ValueError("Grounded translation source/ID mismatch"))
    assert engine.last_request["validation"] == "rejected"
    assert engine.profile.speculative == "none"
    assert engine.fallbacks[0]["from"]["speculative"] == "draft-mtp"
    assert "source/ID mismatch" in engine.log.getvalue()
    assert not engine.reject(ValueError("still invalid"))
    assert starts == ["none"] and len(stops) == 1


def test_oversized_prompt_does_not_restart_engine_or_reject_previous_request(tmp_path, monkeypatch):
    engine = translation.Llama("index-translate-9b", tmp_path, speculative="draft-mtp")
    previous = {"validation": "accepted"}
    engine.statistics.append(previous)
    engine.last_request = previous

    class Response:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    class Client:
        def post(self, path, json):
            if path == "/apply-template":
                return Response({"prompt": "oversized"})
            assert path == "/tokenize"
            return Response({"tokens": list(range(inference.CONFIG["context"]))})

    engine.client = Client()
    monkeypatch.setattr(engine, "fallback", lambda _: pytest.fail("Must split, not restart"))
    with pytest.raises(ValueError) as error:
        engine.generate(
            {"lines": [{"id": "a", "text": "はい"}], "notes": "", "glossary": {}, "before": [], "after": []}
        )
    assert not engine.reject(error.value)
    assert previous["validation"] == "accepted"


@pytest.mark.parametrize("name", ["index-translate-9b", "index-translate-ownsei-sft48-q4-k-m"])
def test_startup_fallback_keeps_history_and_cleanup(tmp_path, monkeypatch, name):
    binary = tmp_path / ".tools/llama/llama-server"
    binary.parent.mkdir(parents=True)
    binary.touch()
    model = tmp_path / models.GGUF["index-translate-9b"]
    model.touch()
    monkeypatch.setattr(inference.backend, "binary", lambda _: binary)
    monkeypatch.setattr(inference.backend, "device", lambda _: "Vulkan0")
    adapter = tmp_path / "adapter.gguf" if "ownsei-sft" in name else None
    if adapter:
        adapter.touch()
    monkeypatch.setattr(inference, "translation_files", lambda _: (model, adapter))
    monkeypatch.setattr(
        inference.subprocess,
        "run",
        lambda *a, **k: type("Version", (), {"stdout": "b11541", "stderr": ""})(),
    )
    starts = []
    stops = []

    def start(engine):
        starts.append(engine.profile.speculative)
        assert engine.model == name and engine.lora == adapter
        if engine.profile.speculative != "none":
            raise RuntimeError("MTP head unavailable")

    monkeypatch.setattr(translation.Llama, "_start_server", start)
    monkeypatch.setattr(translation.Llama, "_stop_server", lambda _: stops.append(True))
    directory = tmp_path / "acceptance"
    with translation.Llama(name, directory, speculative="draft-mtp") as engine:
        assert engine.profile.speculative == "none"
    report = json.loads((directory / "last-inference-metrics.json").read_text())
    assert starts == ["draft-mtp", "none"] and len(stops) == 2
    assert report["effective_profile"]["speculative"] == "none"
    assert report["fallbacks"][0]["reason"] == "MTP head unavailable"
    assert (directory / f"inference-{report['run_id']}.json").exists()
    assert engine.log.closed


def test_cancel_during_load_does_not_attempt_acceleration_fallback(tmp_path, monkeypatch):
    binary = tmp_path / ".tools/llama/llama-server"
    binary.parent.mkdir(parents=True)
    binary.touch()
    (tmp_path / models.GGUF["index-translate-9b"]).touch()
    monkeypatch.setattr(inference.backend, "binary", lambda _: binary)
    monkeypatch.setattr(inference.backend, "device", lambda _: "Vulkan0")
    monkeypatch.setattr(
        inference, "translation_files", lambda _: (tmp_path / models.GGUF["index-translate-9b"], None)
    )
    monkeypatch.setattr(
        inference.subprocess, "run", lambda *a, **k: type("Version", (), {"stdout": "test", "stderr": ""})()
    )

    def start(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(translation.Llama, "_start_server", start)
    monkeypatch.setattr(translation.Llama, "fallback", lambda *a: pytest.fail("Cancelled"))
    with pytest.raises(KeyboardInterrupt):
        with translation.Llama("index-translate-9b", tmp_path / "out", speculative="draft-mtp"):
            pytest.fail("Never ready")


def test_semantic_hints_keep_acceleration_and_preserve_manual(tmp_path, monkeypatch):
    monkeypatch.setattr("onsei.translation_names.build_name_memory", lambda *a, **kw: [])
    store = Store.create(tmp_path / "project", "test")
    track = Track(
        id="t", name="test", source="/fake", sha256="h", duration_ms=4000, sample_rate=16000, channels=1
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
                end_ms=i * 1000 + 500,
                ja_raw="ホームズくんのこと?",
            )
            for i in range(2)
        ],
    )
    store.update_settings({"glossary": {"ホームズくん": "霍爾姆斯君"}})
    store.edit("c0", {"zh_override": "人工校對"})
    calls = []
    events = []

    class Engine:
        profile = "draft-mtp"

        def __init__(self, *_, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def generate(self, data):
            calls.append((self.profile, data))
            text = "霍爾姆斯學長嗎？" if self.profile != "none" else "霍爾姆斯君嗎？"
            return json.dumps([{"id": row["id"], "text": text} for row in data["lines"]])

        def reject(self, error):
            pytest.fail("Semantic hints must not reject inference")

    monkeypatch.setattr(translation, "Llama", Engine)
    translation.translate(store, "t", "index-translate-9b", None, events.append)
    assert [profile for profile, _ in calls] == ["draft-mtp"]
    assert [cue.zh for cue in store.cues("t")] == ["人工校對", "霍爾姆斯學長嗎？"]
    assert "translation_glossary_question" in store.cue("c1").review_flags
    assert not any("已切回一般推論" in e["message"] for e in events)
    caches = list((store.path / "cache/t/translation").glob("*.json"))
    assert len(caches) == 1
    assert all("學長" in row["text"] for row in json.loads(caches[0].read_text())["lines"])


def test_structurally_valid_cache_with_semantic_hint_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr("onsei.translation_names.build_name_memory", lambda *a, **kw: [])
    store = Store.create(tmp_path / "project", "cache validation")
    track = Track(
        id="t", name="test", source="/fake", sha256="h", duration_ms=1000, sample_rate=16000, channels=1
    )
    store.save_track(track)
    store.replace_cues(
        track,
        "v",
        [
            Cue(
                id="c",
                track_id="t",
                version="v",
                ordinal=0,
                start_ms=0,
                end_ms=900,
                ja_raw="ホームズくんのこと?",
            )
        ],
    )
    store.update_settings({"glossary": {"ホームズくん": "霍爾姆斯君"}})
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
            return '[{"id":"c","text":"霍爾姆斯君嗎？"}]'

    monkeypatch.setattr(translation, "Llama", Engine)
    translation.translate(store, "t", "index-translate-9b", None, lambda _: None)
    cue = store.cue("c")
    cache = store.path / "cache/t/translation" / f"{cue.provenance['translation']['cache_key']}.json"
    data = json.loads(cache.read_text())
    data["lines"][0]["text"] = "霍爾姆斯學長嗎？"
    cache.write_text(json.dumps(data))
    cue.translation_status = "stale"
    store.save_generated(cue, cue.revision)
    translation.translate(store, "t", "index-translate-9b", None, lambda _: None)
    assert len(calls) == 1
    assert store.cue("c").zh == "霍爾姆斯學長嗎？"
    assert "translation_glossary_question" in store.cue("c").review_flags
    assert not cache.with_suffix(".invalid.json").exists()
