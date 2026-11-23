import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from onsei.api import JobRequest
from onsei.context import CONTEXT_SIZES, translation_options
from onsei.domain import Cue, Track
from onsei.models import ASR_MODELS, TRANSLATION_MODELS, DEFAULT_TRANSLATION
from onsei.project_copy import copy_project
from onsei.storage import Store
from onsei.translation_adapters import build_prompt


@pytest.mark.parametrize("model", sorted(TRANSLATION_MODELS))
@pytest.mark.parametrize("size", CONTEXT_SIZES)
def test_all_translators_accept_the_same_context_choices(model, size):
    body = JobRequest(action="translate", translation=model, translation_options={"context": size})
    options = translation_options(
        {"translation_options": body.translation_options.model_dump(exclude_none=True)}, model
    )
    assert options["context"] == size
    assert options["names"] == (model != DEFAULT_TRANSLATION)
    assert options["history"] == (model == DEFAULT_TRANSLATION)


def test_catalog_contains_only_the_inference_scope():
    assert ASR_MODELS == {"haas-ja-v1.0", "haas-ja-v1.0-f16", "whisper-ja-760m", "whisper-ja-1.5b"}
    assert TRANSLATION_MODELS == {
        "index-nativelong-9b",
        "index-translate-9b",
        "index-translate-ownsei-sft48-q4-k-m",
        "index-translate-ownsei-sft48-q5-k-m",
    }
    assert JobRequest().asr == "haas-ja-v1.0-f16" and JobRequest().translation == DEFAULT_TRANSLATION


@pytest.mark.parametrize(
    "removed", ["extra_alignment", "use_vad", "after_review", "use_reference", "review_model"]
)
def test_removed_stages_are_not_silently_accepted(removed):
    with pytest.raises(ValidationError):
        JobRequest(**{removed: True})


@pytest.mark.parametrize("model", sorted(TRANSLATION_MODELS))
def test_adapters_keep_global_document_ids_and_explicit_terms(model):
    data = {
        "notes": "",
        "before": [],
        "after": [],
        "glossary": {"レン": "蓮"},
        "global_glossary": [{"ja": "レン", "zh": "倫", "enabled": True}],
        "document": [{"id": "65", "text": "レンくん、こんにちは"}],
        "document_history": [{"id": "64", "ja": "レン", "zh": "蓮"}],
        "lines": [{"id": "stored-id", "ordinal": 65, "text": "レンくん、こんにちは"}],
    }
    prompt, lines, mapping = build_prompt(model, data)
    assert mapping == {"65": "stored-id"} and lines[0]["id"] == "65"
    assert '"レン":"蓮"' in prompt and '"レン":"倫"' not in prompt
    assert "stored-id" not in prompt and "レンくん、こんにちは" in prompt


def test_copy_project_preserves_legacy_results_without_inheriting_active_jobs(tmp_path):
    source = Store.create(tmp_path / "old", "original")
    track = Track(
        id="t",
        name="name",
        source="/original/audio.wav",
        sha256="h",
        duration_ms=1000,
        sample_rate=16000,
        channels=1,
    )
    source.save_track(track)
    source.replace_cues(
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
                ja_raw="はい",
                zh_auto="原譯文",
                translation_status="translated",
            )
        ],
    )
    source.edit("c", {"zh_override": "人工校訂"})
    source.new_job("transcribe", {"asr": "haas-ja-v1.0"})
    before = hashlib.sha256((source.path / "project.db").read_bytes()).hexdigest()
    copied = copy_project(source.path, tmp_path / "copy")
    assert copied.cue("c").zh == "人工校訂" and copied.track("t").source == "/original/audio.wav"
    assert not copied.active_jobs()
    copied.edit("c", {"zh_override": "新版修改"})
    assert source.cue("c").zh == "人工校訂" and source.active_jobs()
    assert hashlib.sha256((source.path / "project.db").read_bytes()).hexdigest() == before


def test_copy_route_keeps_the_original_project_separate(tmp_path, monkeypatch):
    import onsei.api as api

    source = Store.create(tmp_path / "old", "old")
    monkeypatch.setattr(api, "ROOT", tmp_path / "new")
    monkeypatch.setattr(api, "REGISTRY", tmp_path / "new/workspace/index.db")
    client = TestClient(api.app, base_url="http://127.0.0.1")
    response = client.post("/api/projects", json={"title": "copy", "copy_from": str(source.path)})
    assert response.status_code == 200
    with sqlite3.connect(api.REGISTRY) as db:
        copied_path = Path(db.execute("SELECT path FROM projects").fetchone()[0])
    assert copied_path != source.path and copied_path.is_relative_to(api.ROOT)
    assert source.settings()["title"] == "old"


def test_japanese_correction_keeps_native_timestamps_and_manual_time_edits(tmp_path):
    store = Store.create(tmp_path / "native", "native")
    track = Track(
        id="t", name="audio", source="/audio.wav", sha256="h", duration_ms=2000, sample_rate=16000, channels=1
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
                start_ms=100,
                end_ms=1000,
                ja_raw="はい",
                provenance={"model": "haas-ja-v1.0"},
            )
        ],
    )
    edited = store.edit("c", {"ja_override": "はい、先輩"})
    assert (edited.start_ms, edited.end_ms, edited.timing_status) == (100, 1000, "asr")
    edited = store.edit("c", {"ja_override": "先輩", "start_ms": 200})
    assert edited.timing_status == "manual" and edited.start_ms == 200
