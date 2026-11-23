"""Diagnostics must capture the actual native request, including failed requests."""

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from onsei.translation_config import CONFIG
from onsei.inference import Llama
from onsei.translation_diagnostics import TranslationResponseError


class Response:
    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        pass

    def json(self):
        return self.value

    def iter_lines(self):
        yield "data: " + json.dumps(self.value)
        yield ""


class Client:
    def __init__(self, *, failure=None, oversized=False):
        self.calls = []
        self.failure = failure
        self.oversized = oversized

    @contextmanager
    def stream(self, method, path, json):
        yield self.post(path, json)

    def get(self, path, **kwargs):
        assert path == "/slots"
        return Response([{"is_processing": False}])

    def post(self, path, json):
        self.calls.append((path, json))
        if path == "/apply-template":
            return Response({"prompt": "actual-native-template"})
        if path == "/tokenize":
            return Response({"tokens": list(range(CONFIG["context"])) if self.oversized else [1, 2, 3]})
        if self.failure == "transport":
            raise httpx.ReadTimeout("request timed out")
        return Response(
            {
                "content": (
                    '[{"id":"1","source":"こんにちは","text":"' + "嗯" * 200
                    if self.failure == "repetition"
                    else '[{"id":"1","source":"こんにちは","text":"你好"}]'
                ),
                "stop": True,
                "stop_type": "limit" if self.failure in {"truncated", "repetition"} else "eos",
                "tokens_predicted": 512,
            }
        )


def data():
    return {
        "notes": "",
        "glossary": {},
        "before": [],
        "after": [],
        "lines": [{"id": "long-source-id", "text": "こんにちは"}],
    }


def test_request_audit_matches_sent_native_completion_and_correlates_reply(tmp_path):
    engine = Llama("index-translate-9b", tmp_path, speculative="none")
    engine.client = Client()
    engine.generate(data())
    metadata = engine.execution_metadata()
    audit = json.loads(Path(metadata["request_file"]).read_text())
    assert audit["completion"] == engine.client.calls[-1][1]
    assert audit["completion"]["prompt"] == "actual-native-template"
    assert audit["mapping"] == {"1": "long-source-id"}
    assert audit["input_tokens"] == 3 and audit["output_budget"] == engine.client.calls[-1][1]["n_predict"]
    assert metadata["request_id"] == audit["request_id"]
    assert metadata["prompt_sha256"] == audit["prompt_sha256"]
    reply = tmp_path / f"request-{audit['request_id']}.reply.txt"
    assert json.loads(reply.read_text())[0]["source"] == "こんにちは"


def test_request_audit_records_the_effective_terms_actually_sent_to_model(tmp_path):
    engine = Llama("index-translate-9b", tmp_path, speculative="none")
    engine.client = Client()
    value = {
        **data(),
        "global_glossary": [
            {"ja": "こんにちは", "zh": "午安", "enabled": True},
            {"ja": "無關詞", "zh": "不應送出", "enabled": True},
        ],
    }
    engine.generate(value)
    metadata = engine.execution_metadata()
    audit = json.loads(Path(metadata["request_file"]).read_text())
    assert audit["effective_glossary"] == metadata["effective_glossary"] == {"こんにちは": "午安"}
    background = audit["instruction"].split("【参考上下文：只供理解，不要输出】\n")[1].split("\n\n")[0]
    assert "術語表" not in json.loads(background)
    assert '"こんにちは":"午安"' in audit["instruction"]
    assert "【硬性要求】指定术语（日文→中文）" in audit["instruction"]
    assert "不應送出" not in audit["instruction"]


@pytest.mark.parametrize("failure", ["transport", "truncated"])
def test_failed_request_is_still_reproducible(tmp_path, failure):
    engine = Llama("index-translate-9b", tmp_path, speculative="none")
    engine.client = Client(failure=failure)
    with pytest.raises((ValueError, httpx.HTTPError)):
        engine.generate(data())
    metadata = engine.execution_metadata()
    audit = json.loads(Path(metadata["request_file"]).read_text())
    assert audit["completion"] == engine.client.calls[-1][1]
    assert len(engine.statistics) == 1
    assert metadata["request_id"] == engine.statistics[-1]["request_id"]


def test_preflight_budget_rejection_has_input_record_but_no_generation(tmp_path):
    engine = Llama("index-translate-9b", tmp_path, speculative="none")
    engine.client = Client(oversized=True)
    with pytest.raises(ValueError, match="context budget"):
        engine.generate(data())
    records = list(tmp_path.glob("request-*.input.json"))
    assert len(records) == 1
    audit = json.loads(records[0].read_text())
    assert audit["input_tokens"] == CONFIG["context"]
    assert engine.last_request is None and engine.statistics == []
    assert "request_id" not in engine.execution_metadata()
    assert all(path != "/completion" for path, _ in engine.client.calls)


@pytest.mark.parametrize("failure,code", [("truncated", "output_limit"), ("repetition", "repetition")])
def test_truncated_request_records_specific_cause_without_discarding_raw_reply(tmp_path, failure, code):
    engine = Llama("index-translate-9b", tmp_path, speculative="none")
    engine.client = Client(failure=failure)
    with pytest.raises(TranslationResponseError) as caught:
        engine.generate(data())
    assert caught.value.code == code
    assert engine.last_request["failure"]["code"] == code
    assert engine.last_request["failure"]["completion_tokens"] == 512
    reply = tmp_path / f"request-{engine.last_request['request_id']}.reply.txt"
    assert reply.exists()
    if code == "repetition":
        assert engine.last_request["failure"]["repeated_chars"] == 200
        assert reply.read_text().endswith("嗯" * 200)


@pytest.mark.parametrize("warm_failure", [False, True])
def test_nativelong_primes_only_shared_document_and_translates_after_warm_failure(tmp_path, warm_failure):
    from onsei.context import NATIVE_LONG
    from onsei.translation_adapters import native_document_prefix

    class NativeClient(Client):
        def post(self, path, json):
            if path == "/apply-template":
                return Response({"prompt": "<user>" + json["messages"][0]["content"] + "</user>"})
            if path == "/completion" and json.get("n_predict") == 1:
                self.calls.append((path, json))
                if warm_failure:
                    raise httpx.ReadError("warmup unavailable")
                return Response({})
            return super().post(path, json)

    engine = Llama(NATIVE_LONG, tmp_path)
    engine.client = NativeClient()
    doc = [{"id": "1", "text": "こんにちは"}, {"id": "2", "text": "明日も"}]
    value = {**data(), "document": doc, "document_history": [{"id": "0", "text": "早安"}]}
    engine.generate(value)
    engine.generate({**value, "reuse_document_prefix": True})
    engine.generate({**value, "reuse_document_prefix": True})
    requests = [r for path, r in engine.client.calls if path == "/completion"]
    assert len(requests) == 4
    assert requests[0] == requests[2] == requests[3]
    assert requests[1]["prompt"] == "<user>" + native_document_prefix("zh-Hant", doc)
    assert requests[0]["prompt"].startswith(requests[1]["prompt"])
    assert engine.counters["document_warmup_failures" if warm_failure else "document_warmups"] == 1
    previous_calls = len(engine.client.calls)
    engine._warm_document({**value, "reuse_document_prefix": True}, {"scope": "window"}, "unused")
    assert len(engine.client.calls) == previous_calls
