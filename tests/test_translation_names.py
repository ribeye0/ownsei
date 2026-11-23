import json
from collections import Counter

import pytest

from onsei.domain import Cue, Track
from onsei.preferences import effective_glossary
from onsei.storage import Store
from onsei.translation_adapters import build_prompt
from onsei.context import NATIVE_LONG
from onsei.translation_names import build_name_memory, merge_names, parse_names
from onsei.translation_pipeline import process_translation


@pytest.fixture(autouse=True)
def isolated_model_inventory(monkeypatch):
    from onsei.context import DOCUMENT_CONFIG

    monkeypatch.setitem(DOCUMENT_CONFIG, "batch_size", 24)
    monkeypatch.setattr("onsei.modelmanager.installed", lambda _: False)


def row(ja="ハル", zh="小春", same_as="", evidence_ids=None):
    return {"ja": ja, "zh": zh, "same_as": same_as, "evidence_ids": evidence_ids or ["1"]}


def add_track(store, track_id="t", text=None):
    track = Track(
        id=track_id,
        name=track_id,
        source="/fake",
        sha256="h",
        duration_ms=100000,
        sample_rate=16000,
        channels=1,
    )
    store.save_track(track)
    store.replace_cues(
        track,
        "v",
        [
            Cue(
                id=f"{track_id}-{i}",
                track_id=track_id,
                version="v",
                ordinal=i,
                start_ms=i * 1000,
                end_ms=i * 1000 + 900,
                ja_raw=ja,
            )
            for i, ja in enumerate(text or ["ハルくん、ありがとう", "ハルちゃん、また明日"])
        ],
    )


class Engine:
    model = "index-translate-9b"
    loads = 0
    requests = []
    summary = None

    def __init__(self, *args, **kwargs):
        self.counts = Counter()

    def __enter__(self):
        type(self).loads += 1
        return self

    def __exit__(self, *_):
        pass

    def record(self, name, **kw):
        self.counts[name] += 1

    def reject(self, *_):
        return False

    def execution_metadata(self):
        return {"test": True}

    def complete_json(self, prompt, grammar, **kw):
        data = json.loads(prompt.rsplit("\n", 1)[1])
        self.requests.append(data)
        if kw["stage"] == "review":
            return json.dumps(
                [
                    {"id": row["id"], "source": row["ja"], "action": "keep"}
                    for row in data["dialogue"]
                    if row["id"] in data["target_ids"]
                ]
            )
        key = next(i for i, text in data["source"].items() if "ハル" in text or "はる" in text)
        ja = "ハル" if "ハル" in data["source"][key] else "はる"
        return json.dumps({"names": [row(ja, "新譯名" if data["known"] else "小春", evidence_ids=[key])]})

    def generate(self, data):
        self.requests.append(data)
        terms = effective_glossary(data, [r["text"] for r in data["lines"]])
        result = []
        for item in data["lines"]:
            name = "小春"
            assert name in terms.values()
            suffix = "醬" if "ちゃん" in item["text"] else "君"
            result.append({"id": item["id"], "text": name + suffix + "，謝謝你"})
        return json.dumps(result)


@pytest.mark.parametrize(
    "bad",
    [
        row(evidence_ids=["99"]),
        row("ハル", "<think>新名字"),
        row("ハル", ""),
        row("ハル", same_as=12),
    ],
)
def test_name_extraction_requires_literal_evidence_and_plain_stems(bad):
    with pytest.raises(ValueError):
        parse_names(json.dumps({"names": [bad]}), {"1": "ハルくん、ありがとう"})


def test_aliases_keep_first_name_and_do_not_merge_missing_syllables():
    sources = {"1": "コウくん", "2": "こーくん", "3": "ウくん"}
    rows = [row("コウ", "航"), row("こー", "光", "コウ", ["2"]), row("ウ", "悠", "コウ", ["3"])]
    names = merge_names([], rows, sources, "t")
    assert [(e["aliases"], e["zh"]) for e in names] == [(["コウ", "こー"], "航"), (["ウ"], "悠")]
    assert len(merge_names(names, [row("こう", "別名")], {"1": "こうくん"}, "t")) == 2


def test_explicit_nickname_can_link_different_readings():
    sources = {"1": "ユウタです。ハルって呼んでね"}
    names = merge_names([], [row("ユウタ", "優太"), row("ハル", "小春", "ユウタ")], sources, "t")
    assert len(names) == 1 and names[0]["aliases"] == ["ユウタ", "ハル"]


def test_user_terms_and_disabled_names_override_automatic_aliases():
    names = [{"id": "p1", "aliases": ["ハル", "はる"], "zh": "小春"}]
    settings = {"automatic_names": names, "global_glossary": []}
    assert effective_glossary(settings, ["はるくん"]) == {"はる": "小春"}
    settings["glossary"] = {"ハル": "春"}
    assert effective_glossary(settings, ["はるちゃん"]) == {"はる": "春", "ハル": "春"}
    settings["glossary_entries"] = [{"ja": "ハル", "zh": "春", "enabled": False}]
    assert effective_glossary(settings, ["はるくん"]) == {}
    settings.pop("glossary_entries")
    settings["target_language"] = "zh-Hans"
    settings["glossary"] = {"ハル": "葉爾"}
    assert effective_glossary(settings, ["はるくん"])["はる"] == "叶尔"


@pytest.mark.parametrize("model", ["index-translate-9b", "index-translate-ownsei-sft48-q5-k-m", NATIVE_LONG])
def test_every_adapter_receives_name_and_preserves_source(model):
    data = {
        "notes": "",
        "glossary": {},
        "before": [],
        "after": [],
        "global_glossary": [],
        "automatic_names": [{"id": "p1", "aliases": ["ハル"], "zh": "小春"}],
        "lines": [{"id": "c1", "text": "ハルちゃん", "ordinal": 1, "start": 0, "end": 1000}],
    }
    prompt, lines, mapping = build_prompt(model, data)
    assert "小春" in prompt and "固定使用名字本體" in prompt and "ハルちゃん" in prompt
    assert lines[0]["text"] == "ハルちゃん" and list(mapping.values()) == ["c1"]


def test_scan_cache_cross_track_reuse_and_source_edit_invalidation(tmp_path):
    store = Store.create(tmp_path / "p", "test")
    add_track(store)
    engine = Engine()
    Engine.requests = []

    def scan(track_id="t"):
        return build_name_memory(
            store, track_id, store.cues(track_id), store.settings(), engine, engine.model, lambda _: None
        )

    first = scan()
    assert first[0]["zh"] == "小春" and len(Engine.requests) == 1
    assert scan() == first and len(Engine.requests) == 1
    add_track(store, "other", ["はるくん、待って"])
    assert scan("other")[0]["zh"] == "小春" and len(Engine.requests) == 1
    registry = json.loads((store.path / "cache/person-names/registry.json").read_text())
    assert registry["names"][0]["evidence"][0]["ja"] == "ハル"
    store.edit("t-0", {"ja_override": "ハルくん、ユウくんも来る？"})
    assert scan()[0]["zh"] == "小春" and len(Engine.requests) == 2
    other_store = Store.create(tmp_path / "other-project", "other")
    add_track(other_store)
    names = build_name_memory(
        other_store, "t", other_store.cues("t"), {}, engine, engine.model, lambda _: None
    )
    assert names[0]["zh"] == "小春" and Engine.requests[-1]["known"] == []


def test_pipeline_pins_memory_for_resumes_and_batches_without_new_model_load(tmp_path, monkeypatch):
    store = Store.create(tmp_path / "p", "test")
    add_track(store, text=["ハルくん、ありがとう", "ハルちゃん、また明日"] * 30)
    Engine.loads, Engine.requests = 0, []
    monkeypatch.setattr("onsei.translation_pipeline.Llama", Engine)
    snapshot = store.input_snapshot("t")
    out = process_translation(store, "t", Engine.model, lambda _: None, snapshot=snapshot)
    metadata = store.run_metadata(out["draft"])
    assert Engine.loads == 1 and metadata["automatic_names"][0]["zh"] == "小春"
    translations = [r for r in Engine.requests if "lines" in r]
    assert len(translations) > 1
    assert all(r["automatic_names"] == translations[0]["automatic_names"] for r in translations)
    assert [c.zh for c in store.cues("t")][:2] == ["小春君，謝謝你", "小春醬，謝謝你"]
    assert all(c.ja == snapshot["cues"][i]["ja_raw"] for i, c in enumerate(store.cues("t")))
    before = len(Engine.requests)
    process_translation(store, "t", Engine.model, lambda _: None, snapshot=snapshot, outputs=out)
    assert len(Engine.requests) == before and Engine.loads == 1


def test_name_scan_failure_keeps_translation_possible_and_logs_warning(tmp_path, monkeypatch):
    store = Store.create(tmp_path / "p", "test")
    add_track(store)

    def failure(*args, **kw):
        raise ValueError("names failed after retry")

    monkeypatch.setattr("onsei.translation_names.structured_request", failure)
    events = []
    names = build_name_memory(store, "t", store.cues("t"), {}, Engine(), Engine.model, events.append)
    assert names == [] and any("略過" in e["message"] for e in events)
    assert not list((store.path / "cache/person-names").glob("scan-*.json"))


def test_partial_scan_resumes_and_keeps_cross_reading_user_override(tmp_path):
    store = Store.create(tmp_path / "p", "test")
    add_track(store, text=["ハルくん、カフェに行こう"] * 130)
    Engine.requests = []

    def interrupted(event):
        if event["message"].startswith("人物譯名整理 "):
            raise InterruptedError("stop after checkpoint")

    with pytest.raises(InterruptedError):
        build_name_memory(store, "t", store.cues("t"), {}, Engine(), Engine.model, interrupted)
    assert len(Engine.requests) == 1
    names = build_name_memory(store, "t", store.cues("t"), {}, Engine(), Engine.model, lambda _: None)
    assert len(Engine.requests) == 3 and names[0]["zh"] == "小春"
    again = build_name_memory(store, "t", store.cues("t"), {}, Engine(), Engine.model, lambda _: None)
    assert again == names and len(Engine.requests) == 3
    from onsei.translation import payload

    different = [{"id": "p1", "aliases": ["コウ", "こー"], "zh": "航"}]
    cue = store.cues("t")[0].model_copy(update={"ja_raw": "こーくん"})
    data = payload([cue], [cue], {"automatic_names": different, "glossary": {"コウ": "光"}})
    assert effective_glossary(data, [cue.ja])["こー"] == "光"


def test_name_hint_respects_manual_terms_and_never_rejects_or_rewrites():
    from onsei.translation import validate_content

    data = {
        "glossary": {},
        "global_glossary": [],
        "glossary_entries": [],
        "automatic_names": [{"id": "p1", "aliases": ["ハル"], "zh": "小春"}],
        "lines": [{"id": "c1", "text": "ハルくん、ありがとう"}],
    }
    assert validate_content({"c1": "新譯名君，謝謝你"}, data)["c1"] == ["translation_name_question"]
    data["glossary_entries"] = [{"ja": "ハルくん", "zh": "春君", "enabled": True}]
    assert validate_content({"c1": "春君，謝謝你"}, data)["c1"] == []


def test_short_name_is_not_applied_inside_another_persons_name():
    from onsei.translation_names import matching_names

    names = [{"id": "p1", "aliases": ["ハル"], "zh": "小春"}]
    assert matching_names(names, ["ハルカくん"]) == []
    assert matching_names(names, ["スーパーハルくん"]) == []
    assert effective_glossary({"automatic_names": names}, ["ハルくんとハルカくん"]) == {"ハル": "小春"}
    assert parse_names(json.dumps({"names": [row()]}), {"1": "ハルカくん"}) == []
    assert matching_names(names, ["はるくん"]) == names


def test_kana_name_is_not_forced_into_an_ordinary_adverb():
    names = [{"id": "p1", "aliases": ["こう"], "zh": "光"}]
    assert effective_glossary({"automatic_names": names, "global_glossary": []}, ["私はこう思う"]) == {}
    assert effective_glossary(
        {"automatic_names": names, "global_glossary": []}, ["こうくんはどう思う？"]
    ) == {"こう": "光"}


def test_extractor_full_addresses_and_repeated_mentions_become_one_stem():
    raw = {
        "names": [
            row("ハルくん", "小春君"),
            row("ハル", "新名字", evidence_ids=["2"]),
            row("はるちゃん", "小春醬", evidence_ids=["3"]),
        ]
    }
    sources = {"1": "ハルくん、ありがとう", "2": "ハルくんは？", "3": "はるちゃん、またね"}
    parsed = parse_names(json.dumps(raw), sources)
    assert [(r["ja"], r["zh"]) for r in parsed] == [("ハル", "小春"), ("はる", "小春")]


def test_ordinary_translated_words_do_not_become_name_constraints():
    sources = {"1": "太郎さんとケーキを買って帰ろう。ありがとう", "2": "先生、駅はこちらです"}
    data = {
        "names": [
            row("太郎", "太郎"),
            row("ケーキ", "蛋糕"),
            row("ありがとう", "謝謝"),
            row("先生", "老師", evidence_ids=["2"]),
            row("駅", "車站", evidence_ids=["2"]),
            row("駅はこちらです？", "車站在這邊嗎", evidence_ids=["2"]),
        ]
    }
    assert [r["ja"] for r in parse_names(json.dumps(data), sources)] == ["太郎"]


def test_long_vowel_aliases_merge_even_if_extractor_does_not_supply_same_as():
    names = merge_names(
        [],
        [row("コウ", "航"), row("こー", "光", evidence_ids=["2"])],
        {"1": "コウくん", "2": "こーくん"},
        "t",
    )
    assert len(names) == 1 and names[0]["aliases"] == ["コウ", "こー"] and names[0]["zh"] == "航"


def test_scan_cache_is_shared_across_translation_models_and_restores_registry(tmp_path):
    store = Store.create(tmp_path / "p", "test")
    add_track(store, text=["ハルくん、カフェに行こう"])
    Engine.requests = []
    names = build_name_memory(store, "t", store.cues("t"), {}, Engine(), Engine.model, lambda _: None)
    assert len(Engine.requests) == 1
    (store.path / "cache/person-names/registry.json").unlink()

    class NoRequests(Engine):
        def complete_json(self, *a, **kw):
            pytest.fail("A second translation model must reuse the completed name scan")

    cached = build_name_memory(
        store, "t", store.cues("t"), {}, NoRequests(), "index-translate-9b", lambda _: None
    )
    assert names == cached
    assert json.loads((store.path / "cache/person-names/registry.json").read_text())["names"]


def test_asr_long_vowel_spelling_change_retains_established_canonical_name():
    from onsei.translation_names import matching_names

    names = [{"id": "p1", "aliases": ["コウ"], "zh": "航"}]
    assert matching_names(names, ["こーくん"], include_readings=True) == names
    assert matching_names(names, ["ウくん"], include_readings=True) == []


def test_cancel_does_not_mark_unfinished_windows_complete(tmp_path, monkeypatch):
    store = Store.create(tmp_path / "p", "test")
    add_track(store)

    def cancel(*args, **kw):
        raise InterruptedError("cancelled")

    monkeypatch.setattr("onsei.translation_names.structured_request", cancel)
    with pytest.raises(InterruptedError):
        build_name_memory(store, "t", store.cues("t"), {}, Engine(), Engine.model, lambda _: None)
    assert not list((store.path / "cache/person-names").glob("scan-*.json"))


def test_naming_style_rebuilds_old_memory_and_prefers_name_without_added_prefix(tmp_path):
    from onsei.translation_names import NAMING_STYLE, NAME_VERSION
    from onsei.util import atomic_json

    store = Store.create(tmp_path / "p", "test")
    add_track(store)
    old = {"version": NAME_VERSION - 1, "names": [{"id": "old", "aliases": ["ハル"], "zh": "小春"}]}
    registry = store.path / "cache/person-names/registry.json"
    atomic_json(registry, old)

    class StyleEngine(Engine):
        def complete_json(self, prompt, grammar, **kw):
            assert NAMING_STYLE in prompt
            assert "不因親暱語氣或ちゃん、くん自行加『小』『阿』" in prompt
            assert "可以自然選用『小＋名字』" not in prompt
            assert json.loads(prompt.rsplit("\n", 1)[1])["known"] == []
            return json.dumps({"names": [row("ハル", "春")]})

    names = build_name_memory(store, "t", store.cues("t"), {}, StyleEngine(), Engine.model, lambda _: None)
    assert names[0]["zh"] == "春"
    assert json.loads(registry.with_suffix(".invalid.json").read_text()) == old
    prompt, _, _ = build_prompt(
        Engine.model,
        {
            "notes": "",
            "glossary": {},
            "before": [],
            "after": [],
            "automatic_names": names,
            "lines": [{"id": "c1", "text": "ハルくん"}],
        },
    )
    assert '"ハル":"春"' in prompt
    assert "不另換漢字或自行增添『小』『阿』等前綴" in prompt


def test_invented_non_person_phrase_does_not_discard_valid_people():
    sources = {"1": "コウくん、ありがとう", "2": "こーくん、本当にありがとう"}
    data = {
        "names": [
            row("コウくん", "小光"),
            row("こーくん", "小光", evidence_ids=["2"]),
            row("こーくんは", "小光的話", evidence_ids=["2"]),
            row("知らない名前", "未知名字"),
        ]
    }
    parsed = parse_names(json.dumps(data), sources)
    assert [(r["ja"], r["zh"]) for r in parsed] == [("コウ", "小光"), ("こー", "小光")]


def test_echoing_known_person_absent_from_window_does_not_drop_present_people():
    known = [{"id": "p1", "aliases": ["ハル"], "zh": "春"}]
    sources = {"1": "レンちゃん、ありがとう"}
    data = {"names": [row("レン", "蓮"), row("ハル", "春")]}
    assert parse_names(json.dumps(data), sources, known) == [row("レン", "蓮")]


def test_relatives_fragments_and_possessive_phrases_do_not_become_people():
    sources = {
        "1": "コウくん、ありがとう",
        "2": "お兄ちゃんと赤ちゃん",
        "3": "こう君のおじさん",
        "4": "ウくん、こちゃん",
        "5": "君子さん、こちらへどうぞ",
        "6": "レン、またね",
    }
    data = {
        "names": [
            row("コウ", "光"),
            row("赤ちゃん", "寶寶", evidence_ids=["2"]),
            row("お兄ちゃん", "哥哥", evidence_ids=["2"]),
            row("こう君のおじ", "光的叔叔", evidence_ids=["3"]),
            row("ウ", "烏", evidence_ids=["4"]),
            row("こ", "小光", evidence_ids=["4"]),
            row("君子", "君子", evidence_ids=["5"]),
            row("レン", "蓮", evidence_ids=["6"]),
        ]
    }
    parsed = parse_names(json.dumps(data), sources)
    assert [(r["ja"], r["zh"]) for r in parsed] == [("コウ", "光"), ("君子", "君子"), ("レン", "蓮")]


def test_long_scanner_reads_contiguous_context_and_cache_skips_inference(tmp_path):
    from onsei.models import DEFAULT_NAMES

    store = Store.create(tmp_path / "p", "long")
    lines = ["レン、こっちだよ"] + ["今日は図書館で本を読みます"] * 200
    add_track(store, text=lines)

    class LongEngine(Engine):
        model = DEFAULT_NAMES

        def complete_json(self, prompt, grammar, **kw):
            data = json.loads(prompt.rsplit("\n", 1)[1])
            assert len(data["source"]) == len(lines)
            assert data["source"]["200"] == lines[199]
            self.counts["calls"] += 1
            return json.dumps({"names": [row("レン", "蓮")]})

    engine = LongEngine()
    result = build_name_memory(store, "t", store.cues("t"), {}, engine, engine.model, lambda _: None)
    assert result[0]["zh"] == "蓮" and engine.counts["calls"] == 1
    assert build_name_memory(store, "t", store.cues("t"), {}, engine, engine.model, lambda _: None) == result
    assert engine.counts["calls"] == 1


def test_pipeline_switches_light_scanner_out_before_translator_and_reuses_cache(tmp_path, monkeypatch):
    from onsei.models import DEFAULT_NAMES

    store = Store.create(tmp_path / "p", "switch")
    add_track(store)
    events, active = [], []

    class SwitchingEngine(Engine):
        def __init__(self, model, directory, **kwargs):
            super().__init__()
            self.model = model

        def __enter__(self):
            assert not active
            active.append(self.model)
            events.append(("load", self.model))
            return self

        def __exit__(self, *_):
            events.append(("unload", active.pop()))

    monkeypatch.setattr("onsei.translation_pipeline.Llama", SwitchingEngine)
    monkeypatch.setattr("onsei.modelmanager.installed", lambda name: name == DEFAULT_NAMES)
    process_translation(store, "t", Engine.model, lambda _: None)
    assert events == [
        ("load", DEFAULT_NAMES),
        ("unload", DEFAULT_NAMES),
        ("load", Engine.model),
        ("unload", Engine.model),
    ]
    assert not active
