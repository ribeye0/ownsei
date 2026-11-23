"""Failure messages must describe the actual recovery, without changing translation policy."""

import json

import httpx
import pytest

from onsei.context import NATIVE_LONG
from onsei.domain import Cue, Track
from onsei.storage import Store
from onsei.translation import translate
from onsei.translation_diagnostics import (
    TranslationResponseError,
    failure_event,
    failure_reason,
    incomplete_response,
    repeated_output,
)


@pytest.mark.parametrize("text", ["嗯" * 64, "再一次" * 32, "......" * 20])
def test_runaway_translation_is_detected_even_in_unclosed_json(text):
    raw = '[{"id":"7","source":"はい","text":"' + text
    details = repeated_output(raw)
    assert details["row_index"] == 1 and details["repeated_chars"] == len(text)


def test_normal_stammering_source_repetition_and_completed_outputs_are_not_rejected():
    assert (
        repeated_output(json.dumps([{"source": "あ" * 500, "text": "嗯、嗯……好啊"}], ensure_ascii=False))
        is None
    )
    assert repeated_output('[{"text":"' + "嗯" * 63) is None
    # This diagnostic only distinguishes causes of an incomplete response. It
    # does not introduce a new semantic rejection rule for completed subtitles.
    assert incomplete_response('[{"text":"' + "嗯" * 200 + '"}]', {"stop_type": "eos"}, 512) is None


def test_escaped_text_and_repetition_after_normal_dialogue():
    raw = json.dumps([{"text": '他說"好"，' + "嗯" * 100}], ensure_ascii=False)
    assert repeated_output(raw)["repeated_chars"] == 100


def test_repetition_identifies_actual_subtitle_number_in_a_noncontiguous_batch():
    error = incomplete_response('[{"text":"好"},{"text":"' + "嗯" * 100, {"stop_type": "limit"}, 512)
    event = failure_event(error, 10, 14, 1, split=True, ordinals=[10, 14])
    assert event["cue_ordinal"] == 14 and "第 14 句，同一片段" in event["message"]


@pytest.mark.parametrize(
    "reply,code",
    [
        ({"stop_type": "limit"}, "output_limit"),
        ({"stopped_limit": True}, "output_limit"),
        ({"stop": False}, "incomplete_output"),
    ],
)
def test_output_limit_and_interrupted_reply_are_distinguished(reply, code):
    error = incomplete_response('[{"text":"你好', {**reply, "tokens_predicted": 512}, 512)
    assert error.code == code
    event = failure_event(error, 1, 64, 1, split=True, request={"request_id": "request-1"})
    assert event["code"] == code and event["action"] == "split"
    assert "拆小重新翻譯" in event["message"] and "嘗試 1/2" not in event["message"]
    assert event["request_id"] == "request-1" and "512/512 tokens" in event["message"]


@pytest.mark.parametrize(
    "error,code",
    [
        (ValueError("Prompt exceeds context budget; split batch"), "context_limit"),
        (ValueError("Grounded translation source/ID mismatch"), "source_mismatch"),
        (ValueError("Grounded translation row count mismatch"), "line_mismatch"),
        (ValueError("Translation contains model tags or serialized JSON fragments"), "polluted_output"),
        (ValueError("Reply must be a JSON array"), "invalid_format"),
        (json.JSONDecodeError("Expecting value", "", 0), "invalid_json"),
        (httpx.ReadTimeout("timeout"), "timeout"),
        (httpx.ConnectError("connection refused"), "connection"),
    ],
)
def test_failure_categories_preserve_original_error(error, code):
    assert failure_reason(error)[0] == code
    event = failure_event(error, 7, 8, 1)
    assert event["error"] == str(error) and event["code"] == code
    assert "將重試（第 2/2 次）" in event["message"]


def test_http_status_and_final_failure_are_explained():
    error = httpx.HTTPStatusError(
        "server failed",
        request=httpx.Request("POST", "http://localhost/completion"),
        response=httpx.Response(503),
    )
    event = failure_event(error, 7, 7, 2)
    assert event["http_status"] == 503 and event["action"] == "failed"
    assert "HTTP 503" in event["message"] and "此句翻譯失敗" in event["message"]


def project(tmp_path, count=2):
    store = Store.create(tmp_path / "p", "diagnostics")
    store.update_settings({"translation_options": {"names": False}})
    track = Track(
        id="t",
        name="test",
        source="audio.wav",
        sha256="h",
        duration_ms=count * 1000,
        sample_rate=16000,
        channels=1,
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
                ja_raw="こんにちは",
            )
            for i in range(count)
        ],
    )
    return store


def test_repeated_response_splits_then_saves_only_accepted_children(tmp_path):
    store = project(tmp_path)
    events, calls = [], []

    class Engine:
        def generate(self, data):
            calls.append(data)
            if len(data["lines"]) > 1:
                raise TranslationResponseError(
                    "repetition", "Translation output incomplete/truncated", repeated_chars=200
                )
            return json.dumps([{"id": r["id"], "text": "你好"} for r in data["lines"]])

    translate(store, "t", NATIVE_LONG, None, events.append, engine=Engine())
    failures = [e for e in events if e.get("code")]
    assert len(failures) == 1 and failures[0]["action"] == "split"
    assert "異常重複" in failures[0]["message"]
    assert [len(d["lines"]) for d in calls] == [2, 1, 1]
    assert all(c.zh == "你好" and c.translation_status == "translated" for c in store.cues("t"))


def test_invalid_batch_retries_before_splitting_and_single_failure_is_actionable(tmp_path):
    store = project(tmp_path)
    events, calls = [], []

    class Engine:
        def generate(self, data):
            calls.append(data)
            return "invalid JSON"

    with pytest.raises(RuntimeError, match="此句翻譯失敗"):
        translate(store, "t", NATIVE_LONG, None, events.append, engine=Engine())
    failures = [e for e in events if e.get("code")]
    assert [e["action"] for e in failures] == ["retry", "split", "retry", "failed"]
    assert all(e["code"] == "invalid_json" for e in failures)
    assert [len(d["lines"]) for d in calls] == [2, 2, 1, 1]
    assert store.cue("c0").translation_status == "failed" and store.cue("c0").zh is None


def test_repetition_isolates_exact_row_and_keeps_full_context_and_completed_history(tmp_path):
    store = project(tmp_path, 6)
    calls, events = [], []
    records = []

    class Engine:
        def record(self, event, **details):
            records.append(event)

        def generate(self, data):
            calls.append(data)
            if len(data["lines"]) == 6:
                raise TranslationResponseError(
                    "repetition", "Translation output incomplete/truncated", row_index=3, repeated_chars=64
                )
            return json.dumps([{"id": r["id"], "text": "你好" + r["id"]} for r in data["lines"]])

    translate(store, "t", NATIVE_LONG, None, events.append, engine=Engine())
    assert [[r["id"] for r in d["lines"]] for d in calls] == [
        ["c0", "c1", "c2", "c3", "c4", "c5"],
        ["c0", "c1"],
        ["c2"],
        ["c3", "c4", "c5"],
    ]
    assert all(len(d["document"]) == 6 for d in calls)
    assert calls[2]["repetition_recovery"] and not calls[1]["repetition_recovery"]
    assert [r["id"] for r in calls[2]["document_history"]] == ["1", "2"]
    assert [e["action"] for e in events if "code" in e] == ["isolate"]
    assert all(c.translation_status == "translated" for c in store.cues("t"))
    assert records.count("isolations") == records.count("retries") == 1


def test_failed_isolated_row_does_not_block_others_and_resume_only_retries_failed_row(tmp_path):
    store = project(tmp_path, 6)
    calls, events = [], []

    class Engine:
        failing = True

        def generate(self, data):
            calls.append(data)
            if self.failing and any(r["id"] == "c2" for r in data["lines"]):
                raise TranslationResponseError(
                    "repetition",
                    "Translation output incomplete/truncated",
                    row_index=3 if len(data["lines"]) > 1 else 1,
                    repeated_chars=64,
                )
            return json.dumps([{"id": r["id"], "text": "你好" + r["id"]} for r in data["lines"]])

    engine = Engine()
    with pytest.raises(RuntimeError, match="其餘台詞已處理；1 句"):
        translate(store, "t", NATIVE_LONG, None, events.append, engine=engine)
    assert [e["action"] for e in events if "code" in e] == ["isolate", "retry", "skip"]
    assert store.cue("c2").translation_status == "failed" and store.cue("c2").zh is None
    before = {c.id: c.zh for c in store.cues("t") if c.id != "c2"}
    assert all(before.values())
    assert [len(d["lines"]) for d in calls] == [6, 2, 1, 1, 3]
    assert calls[2]["recovery_attempt"] == 0 and calls[3]["recovery_attempt"] == 1
    engine.failing = False
    calls.clear()
    translate(store, "t", NATIVE_LONG, None, events.append, engine=engine)
    assert [[r["id"] for r in d["lines"]] for d in calls] == [["c2"]]
    assert {c.id: c.zh for c in store.cues("t") if c.id != "c2"} == before
