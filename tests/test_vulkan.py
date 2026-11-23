"""Hardware-independent contracts at the Vulkan execution boundary."""

import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from onsei import backend, api
from onsei.segmentation import validate_regions
from onsei.speech_worker import timestamp_validation, subtitle_segments


@pytest.mark.parametrize(
    "bad",
    [
        {"asr_device": "CUDA0"},
        {"llm_device": 1},
        {"vad_engine": "unknown"},
        {"whisper_device": "vulkan"},
        {"script": "x"},
    ],
)
def test_execution_rejects_unknown_values(bad):
    with pytest.raises(ValueError):
        backend.normalize_execution(bad)


def test_device_scope_is_restored_and_cpu_is_explicit(monkeypatch):
    monkeypatch.setattr(
        backend, "devices", lambda e: [{"id": "Vulkan2", "name": "Intel"}, {"id": "CPU", "name": "CPU"}]
    )
    assert backend.device("speech") == "Vulkan2"
    with backend.execution_scope({"asr_device": "CPU", "llm_device": "Vulkan2"}):
        assert backend.device("speech") == "CPU" and backend.device("llama") == "Vulkan2"
    assert backend.current()["asr_device"] == "auto"
    with backend.execution_scope({"asr_device": "Vulkan9"}):
        with pytest.raises(ValueError, match="不存在"):
            backend.device("speech")


@pytest.mark.parametrize(
    "engine,output",
    [
        ("speech", "Vulkan0    gpu   AMD Radeon, 16 GB\nCPU cpu CPU"),
        ("llama", "Available devices:\n Vulkan0: Intel Arc (8192 MiB)"),
    ],
)
def test_parse_each_engine_device_inventory(monkeypatch, tmp_path, engine, output):
    backend.devices.cache_clear()
    monkeypatch.setattr(backend, "binary", lambda e: tmp_path / "engine")
    monkeypatch.setattr(backend, "environment", lambda e: {})
    monkeypatch.setattr(
        backend.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=output, stderr="warning"),
    )
    try:
        assert [d["id"] for d in backend.devices(engine)] == ["Vulkan0", "CPU"]
    finally:
        backend.devices.cache_clear()


@pytest.mark.parametrize(
    "regions",
    [
        [{"start": float("nan"), "end": 1}],
        [{"start": 2, "end": 1}],
        [{"start": 0, "end": 2}, {"start": 1, "end": 3}],
        [],
    ],
)
def test_bad_vad_regions_are_not_silently_accepted(regions):
    with pytest.raises(ValueError):
        validate_regions(regions, 4000)


def test_region_clipping_and_native_empty_prefix():
    assert validate_regions([{"start": 0.2, "end": 10}], 1000) == [
        {"index": 0, "start_ms": 200, "end_ms": 1000, "owned_start_ms": 200, "owned_end_ms": 1000}
    ]
    raw = {
        "text": "はい",
        "tokens": [{"start": 0, "end": 0.16, "text": ""}, {"start": 0.4, "end": 0.8, "text": "はい"}],
        "segments": [{"start": 0, "end": 0.8, "text": "はい"}],
    }
    timestamp_validation(raw, 1)
    assert list(subtitle_segments(raw))[0]["start"] == 0.4
    raw["tokens"][1]["end"] = float("inf")
    with pytest.raises(ValueError):
        timestamp_validation(raw, 1)


def test_api_freezes_execution_and_rejects_invalid_before_submission(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "REGISTRY", tmp_path / "index.db")
    monkeypatch.setattr(api, "start_job", lambda *a: None)
    client = TestClient(api.app, base_url="http://127.0.0.1")
    pid = client.post("/api/projects", json={"title": "test", "path": str(tmp_path / "p")}).json()["id"]
    from onsei.domain import Track

    api.project(pid).save_track(
        Track(id="t", name="x", source="/x", sha256="h", duration_ms=1000, sample_rate=16000, channels=1)
    )
    body = {"action": "transcribe", "execution": {"asr_device": "CPU", "vad_engine": "none"}}
    result = client.post(f"/api/projects/{pid}/jobs", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["options"]["execution"] == {
        **backend.DEFAULTS,
        "asr_device": "CPU",
        "vad_engine": "none",
    }
    body["execution"]["asr_device"] = "CUDA0"
    assert client.post(f"/api/projects/{pid}/jobs", json=body).status_code == 422


def test_prototype_import_preserves_edits_versions_and_is_idempotent(tmp_path, monkeypatch):
    from onsei import migrate_prototype as migration

    monkeypatch.setattr(migration, "ROOT", tmp_path)
    monkeypatch.setattr(api, "REGISTRY", tmp_path / "workspace/index.db")
    audio = tmp_path / "test.wav"
    with wave.open(str(audio), "w") as w:
        w.setparams((1, 2, 16000, 0, "NONE", ""))
        w.writeframes(b"\0\0" * 32000)
    folder = tmp_path / "data/runs/a/results/asr"
    folder.mkdir(parents=True)
    rows = [{"id": "1", "start": 0.1, "end": 0.9, "ja": "はい", "zh": ""}]
    (folder / "asr.json").write_text(json.dumps(rows))
    (folder / "machine-asr.json").write_text(json.dumps([{**rows[0], "ja": "はーい"}]))
    tr = folder.parent / "tr"
    tr.mkdir()
    (tr / "bilingual.json").write_text(json.dumps([{**rows[0], "zh": "好的"}]))
    (tr / "machine-bilingual.json").write_text(json.dumps([{**rows[0], "zh": "好"}]))
    state = {
        "projects": [{"id": "p", "title": "Project", "created_at": 123}],
        "tracks": [
            {
                "id": "old",
                "project_id": "p",
                "path": str(audio),
                "name": "test",
                "runs": [
                    {
                        "id": "a",
                        "kind": "asr",
                        "quant": "F16",
                        "created_at": 124,
                        "workspace": str(folder.parents[1]),
                        "result_name": "asr",
                    },
                    {
                        "id": "b",
                        "kind": "translation",
                        "asr_id": "a",
                        "created_at": 125,
                        "workspace": str(folder.parents[1]),
                        "result_name": "tr",
                    },
                ],
            }
        ],
        "jobs": [],
    }
    path = tmp_path / "data/state.json"
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    report = migration.migrate(path)
    assert report["transcripts"] == report["translations"] == 1
    store = api.project("p")
    track = store.tracks()[0]
    cue = store.cues(track.id)[0]
    assert cue.ja_raw == "はーい" and cue.ja == "はい" and cue.zh_auto == "好" and cue.zh == "好的"
    assert store.transcript_versions(track.id)[0]["created_at"] == 124
    assert store.translation_versions(track.id)[0]["created_at"] == 125
    assert path.read_bytes() == before
    assert migration.migrate(path)["projects"] == 0


def test_native_zero_duration_punctuation_is_preserved_without_empty_prefix():
    from onsei.speech_worker import native_words

    result = {
        "tokens": [
            {"text": "", "start": 0, "end": 0.08},
            {"text": "はい", "start": 0.4, "end": 0.8},
            {"text": "?", "start": 0.8, "end": 0.8},
        ]
    }
    assert native_words(result) == [{"text": "はい?", "start": 0.4, "end": 0.8}]


def test_haas_precisions_never_share_checkpoints(tmp_path, monkeypatch):
    from onsei import asr
    from onsei.storage import Store
    from onsei.domain import Track

    store = Store.create(tmp_path / "p", "precision")
    store.save_track(
        Track(
            id="t",
            name="x",
            source="/fake.wav",
            sha256="same",
            duration_ms=1000,
            sample_rate=16000,
            channels=1,
        )
    )
    monkeypatch.setattr(asr, "pcm", lambda *a: tmp_path / "audio.wav")
    monkeypatch.setattr(
        asr,
        "chunks",
        lambda *a: [{"index": 0, "start_ms": 0, "end_ms": 1000, "owned_start_ms": 0, "owned_end_ms": 1000}],
    )
    monkeypatch.setattr(backend, "devices", lambda e: [{"id": "CPU", "name": "CPU"}])
    writes = []

    def invoke(kind, model, audio, items, directory, progress):
        writes.append((model, directory))
        for item in items:
            Path(item["output"]).write_text(
                json.dumps(
                    {"data": {"text": "はい?", "words": [{"text": "はい?", "start": 0.1, "end": 0.8}]}}
                )
            )

    monkeypatch.setattr(asr, "invoke", invoke)
    with backend.execution_scope({"asr_device": "CPU", "vad_engine": "none"}):
        first = asr.transcribe(store, "t", "haas-ja-v1.0", None, lambda e: None)
        second = asr.transcribe(store, "t", "haas-ja-v1.0-f16", None, lambda e: None)
    assert len(writes) == 2 and writes[0][1] != writes[1][1]
    assert first[0].version != second[0].version
    assert first[0].ja == second[0].ja == "はい?"


def test_track_snapshot_keeps_vad_metadata_consistent_with_version_list(tmp_path):
    from onsei.storage import Store
    from onsei.domain import Track, Cue
    from onsei.view import track_view

    store = Store.create(tmp_path / "p", "metadata")
    track = Track(
        id="t", name="test", source="/fake", sha256="h", duration_ms=1000, sample_rate=16000, channels=1
    )
    store.save_track(track)
    metadata = {
        "segmentation": {"engine": "firered", "settings": {"threshold": 0.5}},
        "vad": {"active": True, "settings": {"settings": {"threshold": 0.6}, "min_overlap": 0.5}},
    }
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
                end_ms=500,
                ja_raw="はい",
                provenance=metadata,
            )
        ],
    )
    direct = store.transcript_versions("t")[0]
    snapshot = track_view(store, "t")["versions"][0]
    assert direct["segmentation"] == snapshot["segmentation"] == metadata["segmentation"]
    assert direct["asr_filter"] == snapshot["asr_filter"] == metadata["vad"]["settings"]


def test_cuda_is_translation_only_and_auto_prefers_cuda(monkeypatch):
    inventory = [
        {"id": "Vulkan0", "name": "AMD"},
        {"id": "CUDA0", "name": "NVIDIA"},
        {"id": "CPU", "name": "CPU"},
    ]
    monkeypatch.setattr(
        backend, "devices", lambda engine: inventory if engine == "llama" else [inventory[0], inventory[2]]
    )
    with backend.execution_scope({"llm_device": "auto"}):
        assert backend.device("llama") == "CUDA0"
        assert backend.device("speech") == "Vulkan0"
    with backend.execution_scope({"llm_device": "Vulkan0"}):
        assert backend.device("llama") == "Vulkan0"
    with pytest.raises(ValueError):
        backend.normalize_execution({"asr_device": "CUDA0"})


def test_engine_discovery_rejects_ambiguous_installations(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    monkeypatch.setattr(backend.sys, "platform", "linux")
    monkeypatch.delenv("ONSEI_LLM_BINARY", raising=False)
    for name in ("old", "new"):
        p = tmp_path / "bin/llama" / name / "llama-server"
        p.parent.mkdir(parents=True)
        p.touch()
    with pytest.raises(ValueError, match="Multiple"):
        backend.binary("llama")
    selected = tmp_path / "bin/llama/new/llama-server"
    monkeypatch.setenv("ONSEI_LLM_BINARY", str(selected))
    assert backend.binary("llama") == selected
    with pytest.raises(ValueError, match="Unknown"):
        backend.binary("../other")


def test_windows_engine_paths_use_exe_and_path_not_ld_library_path(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    monkeypatch.setattr(backend.sys, "platform", "win32")
    monkeypatch.delenv("ONSEI_LLM_BINARY", raising=False)
    p = tmp_path / "bin/llama/llama-server.exe"
    p.parent.mkdir(parents=True)
    p.touch()
    library = p.parent / "cuda/cudart.dll"
    library.parent.mkdir()
    library.touch()
    monkeypatch.setenv("PATH", "existing-path")
    env = backend.environment("llama")
    assert backend.binary("llama") == p
    assert env["PATH"] == f"{p.parent};{library.parent};existing-path"
    assert env.get("LD_LIBRARY_PATH") == backend.os.environ.get("LD_LIBRARY_PATH")
