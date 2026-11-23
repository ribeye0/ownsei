"""Native Haas timing, overlap ownership and pinned model preparation."""

import hashlib
import json

import pytest

from onsei import asr, models, modelmanager
from onsei.util import atomic_json, file_hash


def test_pinned_download_receipt_catalog_and_delete(tmp_path, monkeypatch):
    for module in (models, modelmanager):
        monkeypatch.setattr(module, "ROOT", tmp_path)
        monkeypatch.setattr(module, "STATE", tmp_path / "runtime")
    monkeypatch.setattr(models, "setup_runtime", lambda *args: None)
    monkeypatch.setattr("onsei.backend.binary", lambda _: tmp_path / "speech")
    weights = b"test GGUF checkpoint"
    monkeypatch.setitem(
        models.SPEECH_FILES,
        "haas-ja-v1.0",
        (models.SPEECH_FILES["haas-ja-v1.0"][0], hashlib.sha256(weights).hexdigest()),
    )

    def download(repo, revision, directory, patterns, *args, **kwargs):
        assert (repo, revision) == models.REGISTRY["haas-ja-v1.0"][:2]
        assert patterns[0] == models.SPEECH_FILES["haas-ja-v1.0"][0]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / models.SPEECH_FILES["haas-ja-v1.0"][0]).write_bytes(weights)

    monkeypatch.setattr(models, "download_snapshot", download)
    path = models.prepare("haas-ja-v1.0")
    assert models.required_files("haas-ja-v1.0") == [models.SPEECH_FILES["haas-ja-v1.0"][0]]
    assert models.check_manifest("haas-ja-v1.0") and modelmanager.installed("haas-ja-v1.0")
    entry = next(v for v in modelmanager.catalog()["models"] if v["id"] == "haas-ja-v1.0")
    assert entry["role"] == "asr" and entry["dependencies"] == [] and entry["parameters"] == "616M"
    (path / models.SPEECH_FILES["haas-ja-v1.0"][0]).write_bytes(b"corrupt")
    assert not models.check_manifest("haas-ja-v1.0")
    # An attacker-written receipt cannot replace the pinned upstream hash.
    receipt = json.loads((path / "onsei-model.json").read_text())
    receipt["sha256"][models.SPEECH_FILES["haas-ja-v1.0"][0]] = file_hash(
        path / models.SPEECH_FILES["haas-ja-v1.0"][0]
    )
    atomic_json(path / "onsei-model.json", receipt)
    assert not models.check_manifest("haas-ja-v1.0") and not modelmanager.installed("haas-ja-v1.0")
    modelmanager.delete_model("haas-ja-v1.0")
    assert not path.exists()


def test_wrong_download_hash_is_not_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "ROOT", tmp_path)
    monkeypatch.setattr(models, "STATE", tmp_path / "runtime")
    monkeypatch.setattr(models, "setup_runtime", lambda *args: None)
    monkeypatch.setattr("onsei.backend.binary", lambda _: tmp_path / "speech")

    def download(repo, revision, directory, *args, **kwargs):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / models.SPEECH_FILES["haas-ja-v1.0"][0]).write_bytes(b"wrong")

    monkeypatch.setattr(models, "download_snapshot", download)
    with pytest.raises(ValueError, match="speech.cpp model SHA256"):
        models.prepare("haas-ja-v1.0")
    assert not (models.model_dir("haas-ja-v1.0") / models.SPEECH_FILES["haas-ja-v1.0"][0]).exists()


def test_worker_failure_keeps_chunk_error_before_verbose_log_tail(tmp_path, monkeypatch):
    message = "Haas 片段 #41 (959.50–984.50s)：invalid timestamp"

    class Worker:
        def __init__(self, *args, **kwargs):
            kwargs["stderr"].write("Traceback: original error\n" + "upstream info\n" * 400)
            self.stdout = iter([json.dumps({"error": message, "message": message}) + "\n"])

        def poll(self):
            return 1

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(asr.subprocess, "Popen", Worker)
    monkeypatch.setattr(asr.backend, "device", lambda _: "CPU")
    monkeypatch.setattr(asr.backend, "environment", lambda _: {})
    with pytest.raises(RuntimeError) as error:
        asr.invoke(
            tmp_path / "haas-ja-v1.0",
            tmp_path / "audio.wav",
            [{"output": str(tmp_path / "part.json")}],
            tmp_path,
            lambda _: None,
        )
    assert message in str(error.value).split(". See")[0]
    assert "exit 1" in str(error.value)
