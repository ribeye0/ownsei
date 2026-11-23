import json

import httpx
import pytest

from onsei.inference import Llama
from onsei.translation_diagnostics import TranslationResponseError
from onsei.translation_stream import stream_completion


class Stream(httpx.SyncByteStream):
    def __init__(self, events):
        self.events, self.consumed, self.closed = events, 0, False

    def __iter__(self):
        for event in self.events:
            self.consumed += 1
            yield event

    def close(self):
        self.closed = True


def event(content, **fields):
    return ("data: " + json.dumps({"content": content, **fields}, ensure_ascii=False) + "\n\n").encode()


def run(tmp_path, events, lengths=None):
    stream = Stream(events)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream)),
        base_url="http://localhost",
    )
    cancelled = []

    def idle():
        assert stream.closed, "The HTTP stream must be closed before checking the server slot"
        cancelled.append(True)
        return "released"

    return (
        stream,
        cancelled,
        lambda: stream_completion(
            client, {"stream": True, "n_predict": 1000}, tmp_path / "raw.txt", lengths or [5], idle
        ),
    )


def test_stream_reassembles_utf8_and_escaped_quotes_without_changing_text(tmp_path):
    raw = json.dumps([{"id": "1", "source": "はい", "text": '嗯、嗯……他說"好"'}], ensure_ascii=False)
    body = event(raw, stop=False, tokens_predicted=20) + event(
        "", stop=True, stop_type="eos", tokens_predicted=20
    )
    stream, cancelled, call = run(tmp_path, [body[:47], body[47:49], body[49:]])
    result = call()
    assert result["content"] == raw and (tmp_path / "raw.txt").read_text() == raw
    assert stream.closed and not cancelled


def test_runaway_stops_before_consuming_the_remaining_stream_and_keeps_raw_audit(tmp_path):
    prefix = '[{"id":"1","source":"はい","text":"'
    events = [event(prefix, stop=False)] + [
        event("嗯" * 8, stop=False, tokens_predicted=i * 8) for i in range(1, 100)
    ]
    stream, cancelled, call = run(tmp_path, events)
    with pytest.raises(TranslationResponseError) as caught:
        call()
    assert caught.value.code == "repetition"
    assert caught.value.details["early_stopped"] and caught.value.details["cancellation"] == "released"
    assert caught.value.details["completion_tokens"] == 64
    assert stream.consumed == 9 and stream.closed and cancelled == [True]
    assert (tmp_path / "raw.txt").read_text() == prefix + "嗯" * 64


def test_long_source_repetition_does_not_trigger_guard_and_natural_repetition_is_preserved(tmp_path):
    raw = json.dumps([{"id": "1", "source": "あ" * 300, "text": "嗯" * 100}], ensure_ascii=False)
    stream, cancelled, call = run(tmp_path, [event(raw, stop=False), event("", stop=True)], lengths=[100])
    assert call()["content"] == raw and not cancelled and stream.closed


@pytest.mark.parametrize("ending", [b"", b"data: [DONE]\n\n"])
def test_unfinished_stream_is_not_accepted_even_when_json_is_parseable(tmp_path, ending):
    stream, cancelled, call = run(tmp_path, [event('[{"text":"你好"}]', stop=False), ending])
    with pytest.raises(TranslationResponseError, match="incomplete/truncated"):
        call()
    assert cancelled and stream.closed


def test_sse_server_error_is_reported_and_generation_is_cancelled(tmp_path):
    stream, cancelled, call = run(tmp_path, [b'data: {"error":"failed"}\n\n'])
    with pytest.raises(TranslationResponseError) as caught:
        call()
    assert caught.value.code == "service_error" and cancelled and stream.closed


def test_slot_release_is_checked_and_stuck_generation_restarts_owned_server(tmp_path, monkeypatch):
    engine = Llama("index-nativelong-9b", tmp_path)
    stops, starts = [], []
    engine.client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[{"is_processing": True}])),
        base_url="http://localhost",
    )
    import onsei.inference as inference

    times = iter([0, 0.1, 3])
    monkeypatch.setattr(inference.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(inference.time, "sleep", lambda _: None)
    monkeypatch.setattr(engine, "_stop_server", lambda: stops.append(True))
    monkeypatch.setattr(engine, "_start_server", lambda: starts.append(True))
    assert engine._ensure_idle() == "restarted"
    assert stops == starts == [True] and engine.counters["cancellation_restarts"] == 1


def test_retry_only_enables_dry_and_uses_audited_recovery_seed(tmp_path):
    from test_translation_request_audit import Client, data

    engine = Llama("index-nativelong-9b", tmp_path)
    engine.client = Client()
    engine.generate(data())
    normal = engine.client.calls[-1][1]
    assert normal["stream"] and normal["temperature"] == 0 and "dry_multiplier" not in normal
    engine.generate({**data(), "repetition_recovery": True, "recovery_attempt": 1})
    retry = engine.client.calls[-1][1]
    assert retry["dry_multiplier"] == 0.5 and retry["dry_allowed_length"] == 12
    assert retry["temperature"] == 0.2 and retry["seed"] == 1702
    audit = json.loads(
        next(
            p
            for p in tmp_path.glob("*.input.json")
            if json.loads(p.read_text())["completion"].get("seed") == 1702
        ).read_text()
    )
    assert audit["completion"] == retry


def test_aborted_tokens_are_counted_without_inventing_decoder_timings():
    from onsei.translation_runtime import inference_summary

    summary = inference_summary(
        [
            {"usage": {"completion_tokens": 64}, "finish_reason": "repetition_abort"},
            {"usage": {"completion_tokens": 20}, "timings": {"predicted_n": 20, "predicted_ms": 1000}},
        ],
        {},
        2,
    )
    assert summary["output_tokens"] == 84
    assert summary["decode_tokens_per_second"] == 20
