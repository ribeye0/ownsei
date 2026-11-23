"""Published SFT48 Q4 downloads directly and reuses identical local installations."""

import json

import pytest

from onsei import models, modelmanager
from onsei.translation_runtime import decoding_profile
from onsei.util import atomic_json, file_hash

NAME = "index-translate-ownsei-sft48-q4-k-m"
REPO = "yzk9/index-translate-asmr-9B"
REVISION = "260747b5571a480f9418b7f3d641ee6af75f34c8"
PAYLOAD = b"standalone-model"


@pytest.fixture
def local(tmp_path, monkeypatch):
    for module in (models, modelmanager):
        monkeypatch.setattr(module, "ROOT", tmp_path)
        monkeypatch.setattr(module, "STATE", tmp_path / "state")
    monkeypatch.setattr(modelmanager, "llama_binary", lambda: tmp_path / "llama-server")
    sample = tmp_path / "expected.gguf"
    sample.write_bytes(PAYLOAD)
    monkeypatch.setitem(models.GGUF_SHA256, NAME, file_hash(sample))
    calls = []

    def download(repo, revision, directory, patterns, progress, **kwargs):
        calls.append((repo, revision, patterns, kwargs))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / models.GGUF[NAME]).write_bytes(PAYLOAD)
        (directory / "README.md").write_text("model card")

    monkeypatch.setattr(models, "download_snapshot", download)
    return tmp_path, calls


def test_downloads_only_published_q4_and_card_without_base_or_adapter(local):
    root, calls = local
    directory = models.prepare(NAME)
    assert calls == [(REPO, REVISION, [models.GGUF[NAME], "README.md", "LICENSE*", "NOTICE*"], {"force": False})]
    gguf, adapter = models.translation_files(NAME)
    assert gguf == directory / models.GGUF[NAME] and adapter is None
    assert models.check_manifest(NAME)
    assert not models.model_dir("index-translate-9b").exists()
    row = next(m for m in modelmanager.catalog()["models"] if m["id"] == NAME)
    assert row["ready"] and not row.get("local") and not row["dependencies"]
    assert row["repo"] == REPO and row["url"] == f"https://huggingface.co/{REPO}/tree/{REVISION}"
    assert decoding_profile(NAME).speculative == "none"
    assert decoding_profile(NAME, "draft-mtp", 2).draft_tokens == 2


def test_q5_is_removed_from_selectable_catalog(local):
    assert "index-translate-ownsei-sft48-q5-k-m" not in models.TRANSLATION_MODELS
    assert {m["id"] for m in modelmanager.catalog()["models"] if "sft48" in m["id"]} == {NAME}


def test_identical_legacy_installation_migrates_receipt_without_downloading(local, monkeypatch):
    _, calls = local
    directory = models.model_dir(NAME)
    directory.mkdir(parents=True)
    (directory / models.GGUF[NAME]).write_bytes(PAYLOAD)
    legacy = {
        "repo": "local:Ownsei/Index-Translate-9B-SFT48-GGUF",
        "revision": models.GGUF_SHA256[NAME],
        "sha256": {models.GGUF[NAME]: models.GGUF_SHA256[NAME]},
    }
    atomic_json(directory / "onsei-model.json", legacy)
    # Inventory remains read-only, and no extra download capacity is needed.
    assert models.check_manifest(NAME, verify=False)
    assert json.loads((directory / "onsei-model.json").read_text()) == legacy
    monkeypatch.setattr(models.shutil, "disk_usage", lambda _: pytest.fail("download space needed"))
    assert models.prepare(NAME) == directory
    assert calls == []
    receipt = json.loads((directory / "onsei-model.json").read_text())
    assert receipt["repo"] == REPO and receipt["revision"] == REVISION


def test_corrupted_legacy_file_is_redownloaded_and_verified(local):
    _, calls = local
    directory = models.model_dir(NAME)
    directory.mkdir(parents=True)
    (directory / models.GGUF[NAME]).write_bytes(b"corrupted")
    atomic_json(directory / "onsei-model.json", {
        "repo": "local:Ownsei/Index-Translate-9B-SFT48-GGUF",
        "revision": models.GGUF_SHA256[NAME],
        "sha256": {models.GGUF[NAME]: models.GGUF_SHA256[NAME]},
    })
    assert not models.check_manifest(NAME)
    models.prepare(NAME)
    assert len(calls) == 1 and calls[0][3] == {"force": True}
    assert models.check_manifest(NAME)


def test_download_checksum_failure_does_not_install_model(local, monkeypatch):
    def bad_download(repo, revision, directory, *args, **kwargs):
        directory.mkdir(parents=True)
        (directory / models.GGUF[NAME]).write_bytes(b"wrong weights")

    monkeypatch.setattr(models, "download_snapshot", bad_download)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        models.prepare(NAME)
    assert not modelmanager.installed(NAME)
    assert not (models.model_dir(NAME) / models.GGUF[NAME]).exists()


def test_delete_and_redownload_preserves_original_release_files(local):
    root, calls = local
    published = root / "artifacts/huggingface/published.gguf"
    published.parent.mkdir(parents=True)
    published.write_bytes(PAYLOAD)
    directory = models.prepare(NAME)
    modelmanager.delete_model(NAME)
    assert not directory.exists() and published.read_bytes() == PAYLOAD
    assert models.prepare(NAME) == directory and len(calls) == 2
    assert modelmanager.installed(NAME)
