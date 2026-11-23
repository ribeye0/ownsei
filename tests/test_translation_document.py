import json
from contextlib import contextmanager

import pytest

from onsei.domain import Cue, Track
from onsei.storage import Store
from onsei.inference import Llama
from onsei.translation_config import model_config
from onsei.translation import translate
from onsei.translation_adapters import build_prompt
from onsei.context import NATIVE_LONG, document_batches, document_window
from onsei.translation_runtime import decoding_profile


def cues(count):
    return [
        Cue(
            id=f"c{i}",
            track_id="t",
            version="v",
            ordinal=i,
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            ja_raw="こんにちは",
        )
        for i in range(count)
    ]


def value():
    return {
        "notes": "",
        "glossary": {},
        "before": [],
        "after": [],
        "document": [{"id": str(i), "text": f"文脈{i}"} for i in range(1, 101)],
        "lines": [{"id": "cue-50", "ordinal": 50, "text": "こんにちは"}],
    }


def test_document_batch_coverage_limits_and_native_ids():
    original = cues(150)
    original[70].ja_raw = "あ" * 1700
    batches = document_batches(original)
    assert [cue.id for batch in batches for cue in batch] == [c.id for c in original]
    assert all(len(batch) <= 64 for batch in batches)
    assert all(sum(len(c.ja) for c in batch) <= 1600 or len(batch) == 1 for batch in batches)
    prompt, lines, mapping = build_prompt(
        NATIVE_LONG,
        {
            **value(),
            "target_language": "zh-Hans",
            "global_glossary": [{"ja": "こんにちは", "zh": "午安", "enabled": True}],
        },
    )
    assert mapping == {"50": "cue-50"} and lines[0]["id"] == "50"
    assert "简体中文" in prompt and "午安" in prompt and "文脈1" in prompt and "文脈100" in prompt
    assert model_config(NATIVE_LONG)["context"] == 65536
    assert model_config(NATIVE_LONG)["translation_options"] == {
        "context": 65536,
        "names": False,
        "history": True,
    }
    assert decoding_profile(NATIVE_LONG).speculative == "none"


def test_contiguous_window_preserves_targets_and_source_order():
    document = value()["document"]
    for ids in ({"1", "2"}, {"49", "50"}, {"99", "100"}):
        window = document_window(document, ids, 12)
        assert len(window) == 12 and ids <= {row["id"] for row in window}
        indexes = [document.index(row) for row in window]
        assert indexes == list(range(indexes[0], indexes[0] + len(indexes)))
    with pytest.raises(ValueError, match="missing"):
        document_window(document, {"missing"}, 10)


class Response:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data

    def raise_for_status(self):
        pass

    def iter_lines(self):
        yield "data: " + json.dumps({**self.data, "stop": True})
        yield ""


class Client:
    def __init__(self, oversized=False):
        self.calls = []
        self.oversized = oversized

    @contextmanager
    def stream(self, method, path, json):
        yield self.post(path, json)

    def post(self, path, json):
        self.calls.append((path, json))
        if path == "/apply-template":
            return Response({"prompt": json["messages"][0]["content"]})
        if path == "/tokenize":
            # Force a window with an exact token budget rather than a char cap.
            rows = json["content"].count("文脈")
            return Response({"tokens": [0] * (20000 if self.oversized else rows * 300 + 100)})
        return Response({"content": '[{"id":"50","source":"こんにちは","text":"你好"}]', "stop_type": "eos"})


def test_token_fitted_window_is_audited_without_losing_target(tmp_path):
    engine = Llama(NATIVE_LONG, tmp_path, translation_options={"context": 16384})
    engine.client = Client()
    assert json.loads(engine.generate(value())) == [{"id": "cue-50", "text": "你好"}]
    audit = json.loads(next(tmp_path.glob("request-*.input.json")).read_text())
    context = audit["document_context"]
    assert context["scope"] == "window" and 1 < context["included_lines"] < 100
    assert int(context["first_id"]) <= 50 <= int(context["last_id"])
    assert audit["input_tokens"] + audit["output_budget"] + 64 <= engine.config["context"]
    assert engine.execution_metadata()["document_context"] == context
    completion = next(data for path, data in engine.client.calls if path == "/completion")
    assert completion == audit["completion"] and '"id":"50"' in completion["prompt"]


def test_oversized_targets_are_rejected_before_generation(tmp_path):
    engine = Llama(NATIVE_LONG, tmp_path, translation_options={"context": 16384})
    engine.client = Client(oversized=True)
    with pytest.raises(ValueError, match="context budget"):
        engine.generate(value())
    assert all(path != "/completion" for path, _ in engine.client.calls)


def test_history_is_trimmed_before_japanese_and_audited(tmp_path):
    class HistoryClient(Client):
        def post(self, path, json):
            if path == "/tokenize" and "前譯文" in json["content"]:
                self.calls.append((path, json))
                return Response({"tokens": [0] * 50000})
            return super().post(path, json)

    engine = Llama(NATIVE_LONG, tmp_path, translation_options={"context": 32768})
    engine.client = HistoryClient()
    engine.generate({**value(), "document_history": [{"id": "49", "ja": "前文", "zh": "前譯文"}]})
    audit = json.loads(next(tmp_path.glob("request-*.input.json")).read_text())
    assert audit["document_history"] == []
    assert audit["document_context"]["scope"] == "track"
    assert audit["document_context"]["history_ids"] == []


def test_document_edit_invalidates_distant_inactive_versions_and_preserves_manual(tmp_path):
    store = Store.create(tmp_path / "p", "test")
    track = Track(
        id="t",
        name="test",
        source="audio.wav",
        sha256="abc",
        duration_ms=150000,
        sample_rate=16000,
        channels=1,
    )
    store.save_track(track)
    store.replace_cues(track, "v", cues(150))

    class Engine:
        def generate(self, data):
            assert len(data["document"]) == 150
            return json.dumps([{"id": r["id"], "text": "你好"} for r in data["lines"]])

    translate(store, "t", NATIVE_LONG, None, lambda _: None, engine=Engine())
    native_id = store.cue("c149").provenance["translation_version"]
    store.edit("c149", {"zh_override": "人工"})
    store.prepare_translation("t", "index-translate-9b")
    store.edit("c0", {"ja_override": "こんばんは"})
    with store.db() as db:
        rows = {
            i: json.loads(payload)
            for i, payload in db.execute(
                "SELECT cue_id,payload FROM translation_cues WHERE result_id=?", (native_id,)
            )
        }
    assert rows["c140"]["translation_status"] == "stale"
    assert rows["c149"]["zh_override"] == "人工" and rows["c149"]["translation_status"] == "manual"


def test_retired_format_from_old_jobs_and_browser_requests_uses_standard_protocol():
    from onsei.api import JobRequest, TranslationOptionsRequest

    current = {"context": 16384, "names": False, "history": True}
    standard = model_config(NATIVE_LONG, {"translation_options": current})
    assert standard["protocol"] == "ordered-grounded-json"
    assert "compact_rows" not in TranslationOptionsRequest.model_json_schema()["properties"]
    for previous in (True, False):
        saved = {**current, "compact_rows": previous}
        assert model_config(NATIVE_LONG, {"translation_options": saved}) == standard
        request = JobRequest(translation_options=saved)
        assert request.translation_options.model_dump(exclude_none=True) == current
        assert saved["compact_rows"] is previous
