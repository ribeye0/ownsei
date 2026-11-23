"""Language-specific styles and user terminology, including immutable job inputs."""

import copy
import json

import pytest
from fastapi.testclient import TestClient

from onsei.domain import Cue, Track
from onsei.global_preferences import load_global, save_global
from onsei.preferences import (
    DEFAULT_STYLES,
    effective_glossary,
    pin_translation_settings,
    style_variants,
    translation_preferences,
    updated_preferences,
)
from onsei.storage import Store, ConflictError
from onsei.terminology import normalize_entries
from onsei.translation import payload
from onsei.translation_adapters import build_prompt
from onsei.translation_pipeline import process_translation
from onsei.translation_policy import DEFAULT_GLOSSARY
from onsei.translation_runs import source_key


def entry(ja="語", zh="用詞", enabled=True):
    return {"ja": ja, "zh": zh, "enabled": enabled}


@pytest.fixture
def client(tmp_path, monkeypatch):
    import onsei.api as api

    monkeypatch.setattr(api, "ROOT", tmp_path)
    monkeypatch.setattr(api, "REGISTRY", tmp_path / "workspace/index.db")
    monkeypatch.setattr(api, "start_job", lambda *_: None)
    return TestClient(api.app, base_url="http://127.0.0.1")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("onsei.modelmanager.installed", lambda _: False)
    import onsei.global_preferences as global_settings

    monkeypatch.setattr(global_settings, "GLOBAL_PATH", tmp_path / "global.json")
    s = Store.create(tmp_path / "project", "Test")
    t = Track(
        id="track",
        name="Track",
        source="sample.wav",
        duration_ms=3000,
        sample_rate=16000,
        channels=1,
        sha256="hash",
    )
    s.save_track(t)
    s.replace_cues(
        t,
        "version",
        [
            Cue(
                id="cue",
                track_id=t.id,
                version="version",
                ordinal=0,
                start_ms=100,
                end_ms=1000,
                ja_raw="語ですね",
            )
        ],
    )
    return s


def test_global_empty_persists_and_stale_revision_cannot_overwrite(tmp_path):
    path = tmp_path / "settings.json"
    assert load_global(path)["glossary_entries"] == normalize_entries(DEFAULT_GLOSSARY)
    assert not path.exists()
    first = save_global([], 0, path)
    assert first == {"revision": 1, "glossary_entries": []}
    assert load_global(path) == first
    with pytest.raises(ConflictError):
        save_global([entry()], 0, path)
    assert load_global(path) == first
    path.write_text('{"revision":"1"}')
    with pytest.raises(ValueError, match="格式無效"):
        load_global(path)


@pytest.mark.parametrize(
    "invalid",
    [
        [entry("", "a")],
        [entry("a", "")],
        [entry(enabled=1)],
        [entry(), entry()],
        [{"ja": "a", "zh": "b", "extra": True}],
        None,
    ],
)
def test_invalid_terms_rejected(invalid):
    with pytest.raises(ValueError):
        normalize_entries(invalid)


def test_legacy_terms_convert_and_input_remains_unchanged():
    old = {"語": "用詞"}
    assert normalize_entries(old) == [entry()]
    assert old == {"語": "用詞"}
    assert normalize_entries([entry(" 語 ", " 用詞 ", False)]) == [entry(enabled=False)]


def test_overrides_masks_removal_and_longest_match():
    common = [
        entry("語", "共同用詞"),
        entry("長語", "長詞"),
        entry("未提及", "不要塞入"),
        entry("停用", "不要使用", False),
    ]
    settings = {"global_glossary": common}
    assert effective_glossary(settings, ["長語與停用"]) == {"長語": "長詞"}
    local = {**settings, "glossary_entries": [entry("語", "作品用詞")]}
    assert effective_glossary(local, ["語"]) == {"語": "作品用詞"}
    mask = {**settings, "glossary_entries": [entry(enabled=False)]}
    assert effective_glossary(mask, ["語"]) == {}
    assert effective_glossary({**mask, "glossary_entries": []}, ["語"]) == {"語": "共同用詞"}
    assert effective_glossary({"global_glossary": [], "glossary_entries": []}, ["チンポ"]) == {}


def test_kana_spelling_matches_without_rewriting_source_or_losing_masks():
    common = [entry("チンポ", "肉棒"), entry("おちんちん", "小雞雞"), entry("ちんちん", "雞雞")]
    settings = {"global_glossary": common}
    source = "ちんぽ、チンポ、おチンチン"
    assert effective_glossary(settings, [source]) == {
        "ちんぽ": "肉棒",
        "チンポ": "肉棒",
        "おチンチン": "小雞雞",
    }
    settings["glossary_entries"] = [entry("ちんぽ", "作品用詞")]
    assert effective_glossary(settings, ["チンポ"]) == {"チンポ": "作品用詞", "ちんぽ": "作品用詞"}
    settings["glossary_entries"] = [entry("ちんぽ", "作品用詞", False)]
    assert effective_glossary(settings, ["チンポ、ちんぽ"]) == {}
    settings["glossary_entries"] = [entry("オチンチン", "不使用", False)]
    assert effective_glossary(settings, ["おちんちん、チンチン"]) == {"チンチン": "雞雞"}


def test_explicit_distinct_kana_spellings_are_respected_and_ambiguous_aliases_skipped():
    settings = {"global_glossary": [entry("カナ", "甲"), entry("かな", "乙")]}
    assert effective_glossary(settings, ["カナ、かな、カな"]) == {"カナ": "甲", "かな": "乙"}
    assert effective_glossary(settings, ["仮名"]) == {}


def test_target_switch_uses_separate_prompts_and_preserves_legacy_custom_style():
    old = {"translation_style": "繁體自訂風格"}
    hans = updated_preferences(old, {"target_language": "zh-Hans"})
    assert hans["translation_style"] == DEFAULT_STYLES["zh-Hans"]
    hans = updated_preferences(hans, {"translation_style": "简体专用风格"})
    hant = updated_preferences(hans, {"target_language": "zh-Hant"})
    assert hant["translation_style"] == "繁體自訂風格"
    assert style_variants(hant)["zh-Hans"] == "简体专用风格"
    assert updated_preferences(hant, {"target_language": "zh-Hans"})["translation_style"] == "简体专用风格"
    assert (
        translation_preferences({"target_language": "zh-Hans"})["translation_style"]
        == DEFAULT_STYLES["zh-Hans"]
    )


def test_previous_factory_style_updates_editable_settings_but_not_pinned_jobs():
    current = DEFAULT_STYLES["zh-Hant"]
    previous = current.replace("音聲字幕的繁體中文", "音聲字幕的台灣繁體中文", 1)
    assert previous != current
    for saved in [
        {"translation_style": previous},
        {"translation_styles": {"zh-Hant": previous}},
    ]:
        assert updated_preferences(saved, {})["translation_style"] == current
    custom = previous + "保留我的額外風格設定"
    assert style_variants({"translation_styles": {"zh-Hant": custom}})["zh-Hant"] == custom
    snapshot = {"target_language": "zh-Hant", "translation_style": previous, "global_glossary": []}
    assert translation_preferences(snapshot)["translation_style"] == previous


def test_equivalent_glossary_format_does_not_invalidate_results(store):
    store.update_settings({"glossary": {"語": "用詞"}})
    before = store.input_snapshot("track")
    job = store.new_job("translate", {"track_ids": ["track"]})
    snapshot = job["options"]["input_snapshots"]["track"]
    run = store.new_translation_run(snapshot, "index-translate-9b")
    store.update_settings({"glossary_entries": [entry()]})
    assert source_key(store.input_snapshot("track")) == before["source_key"]
    assert store.snapshot_valid(snapshot) and store.run_metadata(run)["status"] == "pending"
    store.update_settings({"glossary_entries": [entry(enabled=False)]})
    assert not store.snapshot_valid(snapshot)
    assert store.run_metadata(run)["status"] == "stale"
    # Legacy API/directed storage writes must replace, not be shadowed by entries.
    store.update_settings({"glossary": {"新": "新詞"}})
    assert "glossary_entries" not in store.settings()


@pytest.mark.parametrize("model", ["index-translate-9b", "index-translate-9b"])
def test_prompt_payload_hans_factory_style_and_disabled_terms(model, store):
    settings = {
        "target_language": "zh-Hans",
        "global_glossary": normalize_entries(DEFAULT_GLOSSARY),
        "glossary_entries": [entry("チンポ", "不要出现", False)],
    }
    cues = store.cues("track")
    cues[0].ja_raw = "おちんちん、チンポ、粗チン"
    data = payload(cues, cues, settings)
    prompt, _, _ = build_prompt(model, data)
    assert "小鸡鸡" in prompt and "残念鸡鸡" in prompt
    assert "肉棒" not in prompt and "不要出现" not in prompt
    assert DEFAULT_STYLES["zh-Hans"] in prompt and DEFAULT_STYLES["zh-Hant"] not in prompt
    assert data["glossary"] == {}


def test_running_and_resumed_jobs_keep_global_settings_even_if_live_file_invalid(store):
    import onsei.global_preferences as globals

    save_global([entry(zh="初始用詞")], 0)
    options = {"track_ids": ["track"]}
    original = copy.deepcopy(options)
    job = store.new_job("translate", options)
    save_global([entry(zh="之後用詞")], 1)
    second = store.new_job("translate", options)
    assert job["options"]["translation_settings"]["global_glossary"] == [entry(zh="初始用詞")]
    assert second["options"]["translation_settings"]["global_glossary"] == [entry(zh="之後用詞")]
    assert store.snapshot_valid(job["options"]["input_snapshots"]["track"])
    globals.GLOBAL_PATH.write_text("broken")
    resumed = store.new_job("translate", job["options"])
    assert resumed["options"]["translation_settings"] == job["options"]["translation_settings"]
    assert options == original
    assert pin_translation_settings(resumed["options"]["input_snapshots"]["track"]["settings"])[
        "global_glossary"
    ] == [entry(zh="初始用詞")]


def test_global_api_and_per_project_styles_and_job_freezing(client, store):
    result = client.get("/api/preferences/global").json()
    saved = client.put(
        "/api/preferences/global", json={"revision": result["revision"], "glossary_entries": [entry()]}
    ).json()
    assert saved["revision"] == 1
    assert (
        client.put("/api/preferences/global", json={"revision": 0, "glossary_entries": []}).status_code == 409
    )
    p = client.post(
        "/api/projects", json={"title": "Existing", "path": str(store.path), "target_language": "zh-Hans"}
    ).json()["id"]
    base = f"/api/projects/{p}"
    assert client.get(base).json()["settings"]["target_language"] == "zh-Hant"
    assert client.patch(base + "/settings", json={"translation_style": "繁體自訂"}).status_code == 200
    assert client.patch(base + "/settings", json={"target_language": "zh-Hans"}).status_code == 200
    assert client.get(base).json()["settings"]["translation_style"] == DEFAULT_STYLES["zh-Hans"]
    assert (
        client.patch(base + "/settings", json={"translation_styles": {"zh-Hans": "简体自订"}}).status_code
        == 200
    )
    assert client.patch(base + "/settings", json={"target_language": "zh-Hant"}).status_code == 200
    assert client.get(base).json()["settings"]["translation_style"] == "繁體自訂"
    # Batch override selects its language prompt, leaving project preference intact.
    job = client.post(
        base + "/jobs", json={"action": "translate", "track_ids": ["track"], "target_language": "zh-Hans"}
    ).json()
    assert job["options"]["translation_settings"]["translation_style"] == "简体自订"
    assert job["options"]["translation_settings"]["global_glossary"] == [entry()]
    assert client.get(base).json()["settings"]["target_language"] == "zh-Hant"
    assert (
        client.patch(base + "/settings", json={"glossary_entries": [entry(enabled=False)]}).status_code == 200
    )
    assert client.get(base).json()["settings"]["glossary"] == {}
    assert client.patch(base + "/settings", json={"glossary": {"旧": "舊用詞"}}).status_code == 200
    assert client.get(base).json()["settings"]["glossary_entries"] == [entry("旧", "舊用詞")]
    new = client.post("/api/projects", json={"title": "New", "target_language": "zh-Hans"}).json()["id"]
    assert (
        client.get(f"/api/projects/{new}").json()["settings"]["translation_style"]
        == DEFAULT_STYLES["zh-Hans"]
    )
    assert client.post("/api/projects", json={"title": "Bad", "target_language": "en"}).status_code == 422


def test_pipeline_uses_pinned_style_and_terms_and_preserves_old_results(store, monkeypatch):
    import onsei.global_preferences as globals
    import onsei.translation_pipeline as pipeline

    captured = []

    class Engine:
        def __init__(self, model, directory, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def generate(self, data):
            captured.append(data)
            return json.dumps([{"id": row["id"], "text": "這是初始用詞"} for row in data["lines"]])

        def record(self, *args, **kwargs):
            pass

        def execution_metadata(self):
            return {}

    monkeypatch.setattr(pipeline, "Llama", Engine)
    save_global([entry(zh="初始用詞")], 0)
    job = store.new_job("translate", {"track_ids": ["track"]})
    globals.GLOBAL_PATH.write_text("invalid-current-global-settings")
    snapshot = job["options"]["input_snapshots"]["track"]
    outputs = process_translation(store, "track", "index-translate-9b", lambda _: None, snapshot=snapshot)
    metadata = store.run_metadata(outputs["draft"])
    assert metadata["global_glossary"] == [entry(zh="初始用詞")]
    assert effective_glossary(captured[0], ["語"]) == {"語": "初始用詞"}
    assert metadata["status"] == "completed"
    assert store.cues("track")[0].zh == "這是初始用詞"


def test_direct_job_overrides_do_not_use_saved_language_style(store):
    store.update_settings(
        updated_preferences(
            store.settings(), {"translation_styles": {"zh-Hant": "已存繁體", "zh-Hans": "已存简体"}}
        )
    )
    job = store.new_job(
        "translate",
        {
            "track_ids": ["track"],
            "translation_settings": {"target_language": "zh-Hans", "translation_style": "本次专用"},
        },
    )
    assert job["options"]["translation_settings"]["translation_style"] == "本次专用"
    assert store.settings()["translation_styles"]["zh-Hans"] == "已存简体"
    with pytest.raises(ValueError):
        translation_preferences({"target_language": "en", "translation_style": "text", "global_glossary": []})


def test_masked_or_overridden_long_term_does_not_activate_short_substring():
    common = [entry("長語", "長詞"), entry("語", "短詞")]
    mask = {"global_glossary": common, "glossary_entries": [entry("長語", "長詞", False)]}
    assert effective_glossary(mask, ["長語"]) == {}
    assert effective_glossary(mask, ["長語、語"]) == {"語": "短詞"}
    override = {**mask, "glossary_entries": [entry("長語", "作品長詞")]}
    assert effective_glossary(override, ["長語"]) == {"長語": "作品長詞"}
    assert (
        effective_glossary({"global_glossary": [entry("長語", "長詞", False), entry("語", "短詞")]}, ["長語"])
        == {}
    )


def test_full_processing_context_is_frozen_before_asr_and_changed_project_terms_stop_old_job(
    store, monkeypatch
):
    import onsei.translation_pipeline as pipeline

    def no_engine(*args, **kwargs):
        raise AssertionError("inference must not start for changed project terms")

    monkeypatch.setattr(pipeline, "Llama", no_engine)
    store.update_settings({"notes": "原備註", "glossary_entries": [entry()]})
    job = store.new_job("run", {"track_ids": ["track"]})
    context = job["options"]["translation_context"]
    assert context == {"notes": "原備註", "glossary_entries": [entry()], "glossary": {"語": "用詞"}}
    store.update_settings({"glossary_entries": [entry(zh="修改後用詞")]})
    with pytest.raises(ValueError, match="來源已更新"):
        process_translation(
            store,
            "track",
            "index-translate-9b",
            lambda _: None,
            translation_settings=job["options"]["translation_settings"],
            translation_context=context,
        )
    resumed = store.new_job("run", job["options"])
    assert resumed["options"]["translation_context"] == context
