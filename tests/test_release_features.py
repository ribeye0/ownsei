import copy
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from onsei.domain import Cue, Track
from onsei.storage import Store
from onsei.delivery import prepare_delivery, delivery_download
from onsei.locations import compact_location, resolve_location, unique_prefix
from onsei.preferences import translation_preferences, STYLE_INSTRUCTION
from onsei.translation_adapters import build_prompt
from onsei.preferences import effective_glossary
from onsei.translation_policy import DEFAULT_GLOSSARY
from onsei.translation_pipeline import process_translation


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "p", "字幕工具")
    for i in (1, 2):
        t = Track(id=f"track{i}", name=f"音軌{i}", source=str(tmp_path / f"source{i}.wav"), sha256="h",
                  duration_ms=5000, sample_rate=16000, channels=1)
        s.save_track(t)
        s.replace_cues(t, f"version{i}", [Cue(id=f"cue{i}", track_id=t.id, version=f"version{i}", ordinal=0,
                                           start_ms=100, end_ms=1100, ja_raw="こんにちは？", zh_auto="歡迎回來",
                                           translation_status="translated")])
    return s


@pytest.fixture
def client(tmp_path, monkeypatch):
    import onsei.api as api
    root = tmp_path / "app"
    monkeypatch.setattr(api, "ROOT", root)
    monkeypatch.setattr(api, "REGISTRY", root / "workspace" / "index.db")
    monkeypatch.setattr(api, "start_job", lambda *_: None)
    return TestClient(api.app, base_url="http://127.0.0.1")


def test_preferences_validate_and_default_prompt_unchanged():
    data = {"lines": [{"id": "stable", "text": "はい", "start": 0, "end": 500}], "notes": "", "glossary": {}, "before": [], "after": []}
    before = copy.deepcopy(data)
    prompt, _, _ = build_prompt("index-translate-9b", data)
    assert STYLE_INSTRUCTION in prompt and data == before
    custom = {**data, "translation_style": "用自然簡短的對話，不補主語", "target_language": "zh-Hans"}
    changed, _, mapping = build_prompt("index-translate-9b", custom)
    assert "用自然簡短的對話，不補主語" in changed and STYLE_INSTRUCTION not in changed
    assert "简体中文" in changed and "繁体中文" not in changed and mapping == {"1": "stable"}
    assert "source原樣抄寫" in changed
    for invalid in ({"target_language": "en"}, {"translation_style": " "}, {"translation_style": "字" * 2001}):
        with pytest.raises(ValueError):
            translation_preferences(invalid)


@pytest.mark.parametrize("model", ["index-translate-9b", "index-translate-2b", "asmr-qwen3.5-9b", "hy-mt2-7b", "hy-mt2-1.8b"])
@pytest.mark.parametrize("target", ["zh-Hant", "zh-Hans"])
def test_vocabulary_preferences_reach_all_model_prompts_and_allow_override(model, target):
    from onsei.preferences import converter_for
    data = {"lines": [{"id": "stable", "text": " / ".join(DEFAULT_GLOSSARY), "start": 0, "end": 500}],
            "notes": "", "glossary": {"チンポ": "自訂用詞"}, "before": [], "after": [], "target_language": target}
    original = copy.deepcopy(data)
    prompt, _, _ = build_prompt(model, data)
    terms = effective_glossary(data, [data["lines"][0]["text"]])
    convert = converter_for(data).convert
    assert terms == {**{ja: convert(zh) for ja, zh in DEFAULT_GLOSSARY.items()}, "チンポ": convert("自訂用詞")}
    assert data == original
    for ja, zh in terms.items():
        expected = {"ja": ja, "zh": zh} if model == "asmr-qwen3.5-9b" else {ja: zh}
        fragment = json.dumps(expected, ensure_ascii=False, separators=(",", ":"))[1:-1]
        assert fragment in prompt
    # Overlapping short forms do not compete with the longer preference;
    # unrelated/all-ages lines do not receive the vocabulary defaults.
    assert effective_glossary({}, ["おちんちん"]) == {"おちんちん": "小雞雞"}
    assert effective_glossary({}, ["こんにちは"]) == {}


class Engine:
    def __init__(self, model, directory):
        self.model = model
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def generate(self, data):
        return json.dumps([{"id": row["id"], "text": "歡迎回來。"} for row in data["lines"]])
    def record(self, *_args, **_kw):
        pass
    def execution_metadata(self):
        return {}


def test_target_style_pinned_and_old_manual_results_unchanged(store, monkeypatch):
    import onsei.translation_pipeline as pipeline
    monkeypatch.setattr(pipeline, "Llama", Engine)
    parent = store.prepare_translation("track1", "index-translate-9b")
    original = store.cues("track1")[0]
    store.edit(original.id, {"zh_override": "人工校對"})
    options = {"track_ids": ["track1"], "translation_settings": {"target_language": "zh-Hans", "translation_style": "簡短口語"}}
    job = store.new_job("translate", options)
    store.update_settings({"translation_style": "之後的風格", "target_language": "zh-Hant"})
    snapshot = job["options"]["input_snapshots"]["track1"]
    result = process_translation(store, "track1", "index-translate-9b", lambda _: None, snapshot=snapshot)
    meta = store.run_metadata(result["draft"])
    assert meta["target_language"] == "zh-Hans" and meta["translation_style"] == "簡短口語"
    assert store.input_snapshot("track1", result_id=parent)["cues"][0]["zh_override"] == "人工校對"
    assert store.input_snapshot("track1", result_id=result["draft"])["cues"][0]["zh_override"] == "人工校對"
    with pytest.raises(ValueError, match="Checkpoint"):
        store.new_translation_run({**snapshot, "settings": {**snapshot["settings"], "translation_style": "另一風格"}},
                                  "index-translate-9b", identity=result["draft"])


def test_simplified_new_translation_and_traditional_coexist(store, monkeypatch):
    import onsei.translation_pipeline as pipeline
    monkeypatch.setattr(pipeline, "Llama", Engine)
    first = process_translation(store, "track1", "index-translate-9b", lambda _: None)
    second = process_translation(store, "track1", "index-translate-9b", lambda _: None,
                                 translation_settings={"target_language": "zh-Hans", "translation_style": STYLE_INSTRUCTION})
    assert store.input_snapshot("track1", result_id=first["draft"])["cues"][0]["zh_auto"] == "歡迎回來。"
    assert store.input_snapshot("track1", result_id=second["draft"])["cues"][0]["zh_auto"] == "欢迎回来。"
    assert first != second


def test_empty_batch_and_unknown_version_rejected(client, store):
    p = client.post("/api/projects", json={"title": "外部", "path": str(store.path)}).json()["id"]
    base = f"/api/projects/{p}"
    assert client.post(base + "/jobs", json={"track_ids": []}).status_code == 422
    assert client.post(base + "/jobs", json={"action": "translate", "track_ids": ["track1"], "transcript_versions": {"track1": "missing"}}).status_code == 422
    response = client.post(base + "/jobs", json={"action": "translate", "track_ids": ["track1"], "transcript_versions": {"track1": "version1"}, "target_language": "zh-Hans"})
    assert response.status_code == 200
    assert response.json()["options"]["input_snapshots"]["track1"]["version"] == "version1"
    assert client.patch(base + "/settings", json={"translation_style": ""}).status_code == 422
    assert client.get(base + "/tracks/track1/translations?version=version2").json() == []


def test_fresh_batch_generation_is_selected_pinned_and_reusable_on_resume(store):
    options = {"track_ids": ["track1"], "fresh_asr": True}
    first = store.new_job("transcribe", options)
    second = store.new_job("transcribe", options)
    generations = first["options"]["asr_generations"]
    assert set(generations) == {"track1"}
    assert generations != second["options"]["asr_generations"]
    # Resuming a partial job must use the same ASR generation instead of
    # creating yet another version for tracks that have already completed.
    resumed = store.new_job("transcribe", first["options"])
    assert resumed["options"]["asr_generations"] == generations
    assert options == {"track_ids": ["track1"], "fresh_asr": True}


def test_picker_reads_local_directories_without_copying(client, tmp_path):
    directory = tmp_path / "音檔"
    directory.mkdir()
    (directory / "子目錄").mkdir()
    (directory / "a.wav").write_bytes(b"original")
    (directory / "readme.txt").write_text("other")
    result = client.get("/api/local-files", params={"path": str(directory)})
    assert result.status_code == 200
    assert [e["name"] for e in result.json()["entries"]] == ["子目錄", "a.wav"]
    assert (directory / "a.wav").read_bytes() == b"original"
    assert client.get("/api/local-files", params={"path": str(directory / "missing")}).status_code == 422


def test_external_purge_unregisters_only(client, store):
    source = Path(store.track("track1").source)
    source.write_bytes(b"source")
    p = client.post("/api/projects", json={"title": "外部", "path": str(store.path)}).json()["id"]
    base = f"/api/projects/{p}"
    assert client.delete(base + "/purge").status_code == 409
    client.delete(base)
    assert client.get(base + "/purge").json()["scope"] == "registration"
    assert client.delete(base + "/purge").json()["scope"] == "registration"
    assert (store.path / "project.db").exists() and source.read_bytes() == b"source"


def test_owned_purge_rejects_original_inside_work_directory(client):
    p = client.post("/api/projects", json={"title": "管理目錄"}).json()["id"]
    import onsei.api as api
    s = api.project(p)
    source = s.path / "source.wav"
    source.write_bytes(b"original")
    s.save_track(Track(id="t", name="原音", source=str(source), sha256="h", duration_ms=1000, sample_rate=16000, channels=1))
    client.delete(f"/api/projects/{p}")
    assert client.delete(f"/api/projects/{p}/purge").status_code == 422
    assert source.read_bytes() == b"original"


def test_owned_purge_removes_only_its_workspace(client, tmp_path):
    p = client.post("/api/projects", json={"title": "管理目錄"}).json()["id"]
    import onsei.api as api
    directory = api.project(p).path
    (directory / "import-checksums.json").write_text("{}")
    outside = tmp_path / "source.wav"
    outside.write_bytes(b"original")
    client.delete(f"/api/projects/{p}")
    assert client.delete(f"/api/projects/{p}/purge").status_code == 200
    assert not directory.exists() and outside.read_bytes() == b"original"


def test_empty_trash_pins_ids_and_keeps_active_protected_and_external_files(client, store):
    import onsei.api as api
    managed = client.post("/api/projects", json={"title": "可清除"}).json()["id"]
    managed_path = api.project(managed).path
    protected = client.post("/api/projects", json={"title": "受保護"}).json()["id"]
    protected_store = api.project(protected)
    original = protected_store.path / "original.wav"
    original.write_bytes(b"original")
    protected_store.save_track(Track(id="original", name="原音", source=str(original), sha256="h",
                                    duration_ms=1000, sample_rate=16000, channels=1))
    external = client.post("/api/projects", json={"title": "外部", "path": str(store.path)}).json()["id"]
    active = client.post("/api/projects", json={"title": "仍在使用"}).json()["id"]
    later = client.post("/api/projects", json={"title": "確認後才進垃圾桶"}).json()["id"]
    for identity in (managed, protected, external, later):
        assert client.delete(f"/api/projects/{identity}").status_code == 200
    response = client.request("DELETE", "/api/trash/purge", json={"project_ids": [managed, protected, external, active, managed]})
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == [{"id": managed, "scope": "workspace"}, {"id": external, "scope": "registration"}]
    assert {f["id"] for f in data["failed"]} == {protected, active}
    assert not managed_path.exists()
    assert original.read_bytes() == b"original" and (store.path / "project.db").exists()
    assert {p["id"] for p in client.get("/api/projects?trashed=true").json()} == {protected, later}
    assert active in {p["id"] for p in client.get("/api/projects").json()}
    assert client.request("DELETE", "/api/trash/purge", json={"project_ids": []}).status_code == 422


def test_empty_trash_continues_permission_failure_and_reports_remaining(client, monkeypatch):
    import onsei.api as api
    import onsei.localfiles as files
    identities = [client.post("/api/projects", json={"title": title}).json()["id"] for title in ("權限問題", "正常")]
    for identity in identities:
        client.delete(f"/api/projects/{identity}")
    remove = files.remove_workspace
    def fail_first(store):
        if store.path.name == identities[0]:
            raise PermissionError("Cannot remove cache")
        remove(store)
    monkeypatch.setattr(files, "remove_workspace", fail_first)
    data = client.request("DELETE", "/api/trash/purge", json={"project_ids": identities}).json()
    assert data["deleted"] == [{"id": identities[1], "scope": "workspace"}]
    assert data["failed"][0]["id"] == identities[0] and "Cannot remove cache" in data["failed"][0]["error"]
    assert {p["id"] for p in client.get("/api/projects?trashed=true").json()} == {identities[0]}
    with api.registry() as db:
        assert api.registered_project(db, identities[0], include_trash=True).settings()["title"] == "權限問題"


def selection(store, track="track1"):
    return {"track_id": track, "version": store.track(track).active_version,
            "result_id": store.input_snapshot(track)["result_id"]}


def test_zip_conversion_preserves_japanese_and_original_audit(store):
    selected = selection(store)
    before = store.input_snapshot("track1")
    result = prepare_delivery(store, [selected], ["ja", "zh", "bilingual"], ["vtt", "jsonl"], "zh-Hans")
    path, _ = delivery_download(store, result["id"])
    with zipfile.ZipFile(path) as bundle:
        assert "zh-cn/source1.vtt" in bundle.namelist()
        assert "欢迎回来" in bundle.read("zh-cn/source1.vtt").decode()
        assert "こんにちは？" in bundle.read("ja/source1.vtt").decode()
        assert json.loads(bundle.read("source1.jsonl"))["zh_auto"] == "歡迎回來"
    assert store.input_snapshot("track1")["cues"] == before["cues"]
    cue = store.cues("track1")[0]
    store.edit(cue.id, {"zh_override": "新的校對"})
    with pytest.raises(ValueError, match="來源已更新"):
        delivery_download(store, result["id"])


def test_partial_bundle_keeps_valid_japanese(store):
    selected = [selection(store, t) for t in ("track1", "track2")]
    cue = store.cues("track2")[0]
    cue.zh_auto = None
    cue.translation_status = "pending"
    store.save_generated(cue, cue.revision)
    result = prepare_delivery(store, selected, ["ja", "bilingual"], ["vtt"])
    assert result["id"] and result["tracks"][1]["error"]
    path, _ = delivery_download(store, result["id"])
    with zipfile.ZipFile(path) as bundle:
        assert "source2.vtt" in bundle.namelist()
        assert "bilingual/source2.vtt" not in bundle.namelist()


def test_short_reference_resolves_and_extends_on_collision(store):
    loc = {"project": "project1234", "track": "track1", "transcript": "version1", "translation": selection(store)["result_id"], "cue": "cue1"}
    code = compact_location(store, loc, [loc["project"]])
    assert len(code) < 60
    assert code.endswith("/@1")
    assert resolve_location(code, [loc["project"]], lambda _: store) == loc
    assert resolve_location(code.rsplit("/", 1)[0] + "/cue1", [loc["project"]], lambda _: store) == loc
    assert unique_prefix("abcdef0", ["abcdef0", "abcdef1"]) == "abcdef0"
    with pytest.raises(ValueError):
        resolve_location(code, ["project1234", "project9999"], lambda _: store)


def test_batch_continues_content_failure_and_resume_skips_complete_track(store, tmp_path, monkeypatch):
    import onsei.runner as runner
    import onsei.translation_pipeline as pipeline
    import onsei.export as exports
    class Metrics:
        data = {}
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(runner, "GpuMetrics", Metrics)
    monkeypatch.setattr(runner, "STATE", tmp_path / "gpu")
    calls = []
    def translate(_store, track, *_args, **_kw):
        calls.append(track)
        if track == "track1" and calls.count("track1") == 1:
            raise RuntimeError("Translation failed at cue1: invalid content")
    monkeypatch.setattr(pipeline, "process_translation", translate)
    monkeypatch.setattr(exports, "export_track", lambda *_args, **_kw: [])
    job = store.new_job("translate", {"track_ids": ["track1", "track2"], "continue_on_error": True})
    runner.run_job(store, job["id"])
    partial = store.job(job["id"])
    assert partial["status"] == "partial" and partial["track_outcomes"]["track2"]["status"] == "completed"
    assert partial["track_number"] == 2 and partial["tracks_total"] == 2
    retry = runner.resume_job(store, partial)
    runner.run_job(store, retry["id"])
    assert calls == ["track1", "track2", "track1"]
    assert store.job(retry["id"])["status"] == "completed"


def test_batch_stops_shared_model_failure(store, tmp_path, monkeypatch):
    import onsei.runner as runner
    import onsei.translation_pipeline as pipeline
    class Metrics:
        data = {}
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(runner, "GpuMetrics", Metrics)
    monkeypatch.setattr(runner, "STATE", tmp_path / "gpu")
    calls=[]
    def fail(_store, track, *_args, **_kw):
        calls.append(track)
        raise ValueError("Translation model missing; run ownsei models prepare")
    monkeypatch.setattr(pipeline, "process_translation", fail)
    job=store.new_job("translate", {"track_ids": ["track1", "track2"], "continue_on_error": True})
    runner.run_job(store, job["id"])
    assert calls == ["track1"] and store.job(job["id"])["status"] == "failed"
