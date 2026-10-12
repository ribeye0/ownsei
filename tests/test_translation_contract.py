import json

import pytest

from onsei.domain import Cue, Track
from onsei.export import export_track
from onsei.snapshots import fresh_exports, register_export
from onsei.storage import Store
from onsei.translation import CONFIG, PROMPT_VERSION, build_prompt, parse_reply
import onsei.translation as module


@pytest.mark.parametrize(
    "text",
    [
        '我好想快點去社團啊，一直在想會不會讓學長等著呢。」}, {"',
        "原來還有這種事啊……唉。」}]</think_never_used_51bce0c785ca2f68081bfa7d>[",
        "好<|im_end|>",
        "<think>正在思考</think>好的",
        '好的。{"id":"2","text":"多餘"}',
        ":null",
        " : null ",
    ],
)
def test_reject_pollution_inside_valid_json_strings(text):
    with pytest.raises(ValueError):
        parse_reply(json.dumps([{"id": "a", "text": text}]), ["a"])


def test_preserve_real_dialogue_punctuation_and_reject_bad_ids():
    text = "「學長（小聲），怎麼不說話了？」"
    assert parse_reply(json.dumps([{"id": "a", "text": text}]), ["a"]) == {"a": text}
    for id in ([], {}, None, 1):
        with pytest.raises(ValueError):
            parse_reply(json.dumps([{"id": id, "text": "好"}]), ["a"])
    with pytest.raises(ValueError):
        parse_reply('[{"id":"b","text":"好"},{"id":"a","text":"好"}]', ["a", "b"])


def test_real_dialogue_about_null_is_not_a_format_fragment():
    for text in ("null", "這個變數是 null", "答案：null？"):
        assert parse_reply(json.dumps([{"id": "a", "text": text}]), ["a"]) == {"a": text}


@pytest.mark.parametrize("text", ["。", "，", ".,", " 。\n， "])
def test_punctuation_only_reply_is_rejected(text):
    with pytest.raises(ValueError, match="empty translation"):
        parse_reply(json.dumps([{"id": "a", "text": text}]), ["a"])


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "p", "test")
    t = Track(
        id="t", name="校園對話", source="/fake", sha256="h", duration_ms=10000, sample_rate=16000, channels=1
    )
    s.save_track(t)
    cs = [
        Cue(
            id=f"long-stable-{i}",
            track_id="t",
            version="v",
            ordinal=i,
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            ja_raw=ja,
        )
        for i, ja in enumerate(["センパイは私がいると邪魔?", "黙っちゃった。"])
    ]
    s.replace_cues(t, "v", cs)
    return s


def test_prompt_compact_ids_dialogue_and_original_is_preserved(store):
    cues = store.cues("t")
    data = module.payload(
        cues, cues, {"notes": "學妹對學長說話；學長沒有配音", "glossary": {"センパイ": "學長"}}, "校園對話"
    )
    prompt, lines, mapping = build_prompt("index-translate-9b", data)
    assert list(mapping) == ["1", "2"]
    assert lines[1] == {"id": "2", "text": "黙っちゃった"}
    assert "long-stable-" not in prompt and "第三人稱旁白" in prompt
    assert "學妹對學長" in prompt and "校園對話" in prompt
    assert "句尾不加句號或逗號" in prompt
    assert data["lines"][1]["id"] == cues[1].id


def test_upgrade_invalid_cache_retry_and_preserve_manual(store, monkeypatch):
    calls = []

    class Engine:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def generate(self, data):
            calls.append(data)
            if len(calls) == 1:
                return json.dumps([{"id": r["id"], "text": '唉。」}, {"'} for r in data["lines"]])
            return json.dumps([{"id": r["id"], "text": "怎麼不說話了。"} for r in data["lines"]])

    monkeypatch.setattr(module, "Llama", Engine)
    store.edit("long-stable-0", {"zh_override": "人工譯文"})
    module.translate(store, "t", "index-translate-2b", None, lambda _: None)
    assert len(calls) == 2 and calls[1]["retry"]
    assert store.cue("long-stable-0").zh == "人工譯文"
    c = store.cue("long-stable-1")
    assert c.provenance["translation"]["prompt_version"] == PROMPT_VERSION
    module.translate(store, "t", "index-translate-2b", None, lambda _: None)
    assert len(calls) == 2
    cache = store.path / "cache/t/translation" / (c.provenance["translation"]["cache_key"] + ".json")
    cached = json.loads(cache.read_text())
    cached["lines"][1]["text"] += "</think_never_used_bad>"
    cache.write_text(json.dumps(cached))
    c.provenance["translation"]["prompt_version"] = PROMPT_VERSION - 1
    store.save_generated(c, c.revision)
    module.translate(store, "t", "index-translate-2b", None, lambda _: None)
    assert len(calls) == 3 and cache.with_suffix(".invalid.json").exists()
    assert store.cue("long-stable-0").zh == "人工譯文"
    assert store.cue("long-stable-1").zh == "怎麼不說話了"
    assert store.cue("long-stable-1").zh_auto == "怎麼不說話了。"


def test_legacy_polluted_exports_blocked_but_manual_retained(store):
    for c in store.cues("t"):
        c.zh_auto = "好</think_never_used_bad>"
        c.translation_status = "translated"
        store.save_generated(c, c.revision)
    register_export(store, "t", store.cues("t"), ["old.zh-TW.txt", "old.bilingual.srt", "old.ja.txt"])
    assert fresh_exports(store) == ["old.ja.txt"]
    with pytest.raises(ValueError):
        export_track(store, "t", store.path / "exports")
    for c in store.cues("t"):
        store.edit(c.id, {"zh_override": "人工譯文（已校對）"})
    exports = export_track(store, "t", store.path / "exports")
    assert len(exports) == 13
    assert sum(p.suffix == ".vtt" for p in exports) == 3


def test_token_budget_uses_rendered_chat_template(tmp_path):
    class Response:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    class Client:
        def __init__(self):
            self.calls = []

        def post(self, path, json):
            self.calls.append((path, json))
            if path == "/apply-template":
                return Response({"prompt": "rendered-chat-with-special-tokens"})
            if path == "/tokenize":
                return Response({"tokens": list(range(CONFIG["context"]))})
            raise AssertionError("Oversized input must never reach generation")

    engine = module.Llama("index-translate-2b", tmp_path)
    engine.client = Client()
    with pytest.raises(ValueError, match="context budget"):
        engine.generate(
            {
                "lines": [{"id": "stable", "text": "はい。"}],
                "before": [],
                "after": [],
                "notes": "",
                "glossary": {},
            }
        )
    assert engine.client.calls[1][1]["content"] == "rendered-chat-with-special-tokens"
    assert engine.client.calls[0][1]["chat_template_kwargs"] == {"enable_thinking": False}


def test_legacy_pollution_is_visible_as_stale_without_erasing_raw_text(store):
    c = store.cue("long-stable-1")
    c.zh_auto = '原來如此。」}, {"'
    c.translation_status = "translated"
    store.save_generated(c, c.revision)
    current = store.cue(c.id)
    assert current.translation_status == "stale"
    assert current.zh_auto == c.zh_auto
    assert "translation_invalid_output" in current.review_flags
    store.edit(c.id, {"zh_override": "原來如此。"})
    assert store.cue(c.id).translation_status == "manual"
    assert "translation_invalid_output" not in store.cue(c.id).review_flags


def test_native_model_prompt_and_normalization(store):
    data = module.payload(store.cues("t"), store.cues("t"), store.settings(), "校園對話")
    prompt, lines, mapping = build_prompt("asmr-qwen3.5-9b", data)
    assert lines[0]["id"] == 1 and lines[0]["start"] == 0 and lines[0]["end"] == 900
    assert "逐字稿：" in prompt and "不要合併" in prompt and '"ids": [<n>]' in prompt
    rows = [
        {"ids": [line["id"]], "text": "怎麼不說話了？", "start": line["start"], "end": line["end"]}
        for line in lines
    ]
    normalized = module.normalize_native_reply(json.dumps(rows), lines)
    assert parse_reply(normalized, list(mapping)) == {"1": "怎麼不說話了？", "2": "怎麼不說話了？"}
    for bad in ([1, 2], [2], [True], ["1"]):
        changed = json.loads(json.dumps(rows))
        changed[0]["ids"] = bad
        with pytest.raises(ValueError):
            module.normalize_native_reply(json.dumps(changed), lines)
    changed = json.loads(json.dumps(rows))
    changed[0]["start"] = 0.0
    with pytest.raises(ValueError):
        module.normalize_native_reply(json.dumps(changed), lines)
    changed = json.loads(json.dumps(rows))
    changed[0]["end"] += 1
    with pytest.raises(ValueError):
        module.normalize_native_reply(json.dumps(changed), lines)
    with pytest.raises(ValueError):
        module.normalize_native_reply(json.dumps(rows[::-1]), lines)


def test_native_completion_uses_fixed_grammar_and_maps_stable_ids(store, tmp_path):
    class Response:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    class Client:
        def __init__(self):
            self.calls = []

        def post(self, path, json):
            self.calls.append((path, json))
            if path == "/apply-template":
                return Response({"prompt": "model-native-template"})
            if path == "/tokenize":
                return Response({"tokens": [1]})
            assert path == "/completion"
            return Response(
                {
                    "content": '[{"ids":[1],"source":"センパイは私がいると邪魔?","text":"他說\\"你好\\"（小聲）。","start":0,"end":900}]',
                    "stop": True,
                    "stop_type": "eos",
                }
            )

    engine = module.Llama("asmr-qwen3.5-9b", tmp_path)
    engine.client = Client()
    cue = store.cues("t")[0]
    reply = engine.generate(module.payload([cue], [cue], store.settings()))
    assert parse_reply(reply, [cue.id]) == {cue.id: '他說"你好"（小聲）。'}
    request = engine.client.calls[-1][1]
    assert request["prompt"] == "model-native-template"
    assert "grammar" in request and "json_schema" not in request
    assert "row0" in request["grammar"] and "char ::=" in request["grammar"]
    lines = [{"id":1,"text":cue.ja,"start":0,"end":900}]
    with pytest.raises(ValueError,match='source/ID'):
        module.normalize_native_reply(json.dumps([{"ids":[1],"source":"別句", "text":"你好", "start":0,"end":900}]),lines,grounded=True)


def test_unicode_escaped_delimiters_and_serialized_metadata_are_rejected():
    with pytest.raises(ValueError):
        parse_reply(r'[{"id":"a","text":"好的\u007d, \u007b"}]', ["a"])
    with pytest.raises(ValueError):
        parse_reply(json.dumps([{"id": "a", "text": '好的", "highlight_words": "片段'}]), ["a"])


def test_index_profiles_use_one_source_representation_and_ground_each_row(store):
    data = module.payload(store.cues("t"), store.cues("t"), {"notes": "學妹對學長說話", "glossary": {}})
    big, _, _ = build_prompt("index-translate-9b", data)
    small, _, _ = build_prompt("index-translate-2b", data)
    assert "整批連續台詞" not in big and big.count("黙っちゃった") == 1
    assert "source原樣抄寫" in big and "source原樣抄寫" in small
    assert "整批連續台詞" not in small and '"id":"2","text":"黙っちゃった"' in small
    assert "學妹對學長說話" in big and "學妹對學長說話" in small
