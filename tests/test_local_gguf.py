"""Standalone GGUFs must stay distinct from adapters and preserve release sources."""

import errno
import json

import pytest

from onsei import models, modelmanager
from onsei.local_models import LOCAL_GGUFS
from onsei.translation_runtime import decoding_profile
from onsei.util import atomic_json, file_hash


@pytest.fixture(params=list(LOCAL_GGUFS))
def local(tmp_path, monkeypatch, request):
    for module in (models, modelmanager):
        monkeypatch.setattr(module, "ROOT", tmp_path)
        monkeypatch.setattr(module, "STATE", tmp_path / "state")
    monkeypatch.setattr(modelmanager, "llama_binary", lambda: tmp_path / "llama-server")
    monkeypatch.setattr(
        models, "download_snapshot", lambda *a, **k: pytest.fail("Local GGUF tried to download")
    )
    name = request.param
    source = tmp_path / "artifacts/huggingface" / LOCAL_GGUFS[name]["package"] / models.GGUF[name]
    source.parent.mkdir(parents=True)
    source.write_bytes(b"standalone-model")
    monkeypatch.setitem(models.GGUF_SHA256, name, file_hash(source))
    return name, source


def test_prepare_standalone_without_shared_base_or_lora(local):
    name, source = local
    directory = models.prepare(name)
    assert models.check_manifest(name)
    gguf, lora = models.translation_files(name)
    assert gguf == directory / source.name and lora is None
    assert gguf.read_bytes() == source.read_bytes()
    assert not models.model_dir("index-translate-9b").exists()
    entry = next(row for row in modelmanager.catalog()["models"] if row["id"] == name)
    assert entry["ready"] and entry["local"] and entry["local_kind"] == "gguf"
    assert not entry["dependencies"] and entry["url"] is None
    assert decoding_profile(name).speculative == "none"
    assert decoding_profile(name, "draft-mtp", 2).draft_tokens == 2
    assert models.REGISTRY[name][1] == LOCAL_GGUFS[name]["sha256"]


def test_wrong_source_and_wrong_receipt_rejected(local):
    name, source = local
    source.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="checksum"):
        models.prepare(name)
    source.write_bytes(b"standalone-model")
    directory = models.prepare(name)
    receipt = json.loads((directory / "onsei-model.json").read_text())
    receipt["sha256"][models.GGUF[name]] = "wrong"
    atomic_json(directory / "onsei-model.json", receipt)
    assert not modelmanager.installed(name)


def test_delete_preserves_published_file_and_can_prepare_again(local):
    name, source = local
    directory = models.prepare(name)
    modelmanager.delete_model(name)
    assert not directory.exists()
    assert source.read_bytes() == b"standalone-model"
    assert models.prepare(name) == directory
    assert modelmanager.installed(name)


def test_missing_source_never_downloads_or_prepares_original_base(local):
    name, source = local
    source.unlink()
    with pytest.raises(ValueError, match="models/.sources/"):
        models.prepare(name)
    assert not models.model_dir("index-translate-9b").exists()


def test_cross_filesystem_source_copies_instead_of_linking(local, monkeypatch):
    name, source = local

    def cross_device(*args):
        raise OSError(errno.EXDEV, "different filesystem")

    monkeypatch.setattr(models.os, "link", cross_device)
    models.prepare(name)
    gguf, lora = models.translation_files(name)
    assert lora is None and gguf.read_bytes() == source.read_bytes()
    assert gguf.stat().st_ino != source.stat().st_ino
    assert models.check_manifest(name)
