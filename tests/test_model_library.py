import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from filelock import FileLock

from onsei import modelmanager, models
from onsei.api import app
from onsei.model_library import change_library
from onsei.storage import ConflictError
from onsei.user_paths import config_dir, data_dir, download_cache, library_setting, locks_dir, models_dir
from onsei.util import atomic_json, file_hash


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    from onsei import vad_worker

    monkeypatch.setattr(modelmanager, "ROOT", tmp_path)
    monkeypatch.setattr(modelmanager, "STATE", tmp_path / "runtime")
    root = config_dir() / "models"
    model = root / "firered-vad-ncnn"
    model.mkdir(parents=True)
    weights = model / "weights.bin"
    weights.write_bytes(b"test weights")
    hashes = {weights.name: file_hash(weights)}
    monkeypatch.setattr(vad_worker, "HASHES", hashes)
    repo, revision, _ = models.REGISTRY["firered-vad"]
    atomic_json(model / "onsei-model.json", {"repo": repo, "revision": revision, "sha256": hashes})
    return root


def test_platform_defaults_and_local_override(tmp_path, monkeypatch):
    (config_dir() / "models/.cache").mkdir(parents=True)
    assert models_dir() == data_dir() / "models"
    monkeypatch.setattr(sys, "platform", "win32")
    assert models_dir() == Path(os.environ["LOCALAPPDATA"]) / "ownsei/models"
    assert download_cache() == data_dir() / "cache"
    assert library_setting().parent == data_dir() != config_dir()
    if os.name != "nt":
        monkeypatch.setattr(sys, "platform", "linux")
        assert models_dir() == Path(os.environ["XDG_DATA_HOME"]) / "ownsei/models"
        assert download_cache() == Path(os.environ["XDG_CACHE_HOME"]) / "ownsei"


def test_legacy_select_and_move_without_redownload(legacy, tmp_path):
    assert models_dir() == legacy and models.check_manifest("firered-vad")
    other = tmp_path / "other library"
    client = TestClient(app, base_url="http://127.0.0.1")

    def change(target, mode, expected):
        return client.post(
            "/api/models/library", json={"path": str(target), "mode": mode, "expected_source": str(expected)}
        )

    assert change(other, "select", legacy).is_success
    assert not models.check_manifest("firered-vad") and (legacy / "firered-vad-ncnn/weights.bin").is_file()
    assert change(legacy, "select", other).is_success
    assert change(other, "move", other).status_code == 409
    extra = legacy / "firered-vad-ncnn/my-notes.txt"
    extra.write_text("keep")
    assert change(other, "move", legacy).is_success
    assert json.loads(library_setting().read_text())["models_dir"] == str(other)
    assert models.check_manifest("firered-vad") and extra.read_text() == "keep"
    assert not (legacy / "firered-vad-ncnn/weights.bin").exists()
    assert download_cache().parent == other.parent and not download_cache().is_relative_to(other)
    assert change(other, "move", other).is_success
    assert change(legacy, "move", other).is_success
    assert extra.read_text() == "keep" and models.check_manifest("firered-vad")


def test_failed_move_preserves_active_library(legacy, tmp_path):
    target = tmp_path / "new"
    with FileLock(locks_dir() / "gpu.lock"):
        with pytest.raises(ConflictError):
            change_library(str(target), "move")
    (legacy / "firered-vad-ncnn/weights.bin").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="校驗失敗"):
        change_library(str(target), "move")
    assert models_dir() == legacy and not list(target.iterdir())
    assert (legacy / "firered-vad-ncnn/weights.bin").read_bytes() == b"damaged"
    (target / "notes.txt").touch()
    with pytest.raises(ValueError, match="空資料夾"):
        change_library(str(target), "select")


def test_cross_volume_copy_verify_and_switch(legacy):
    volume = Path("/dev/shm")
    if not volume.is_dir() or volume.stat().st_dev == legacy.stat().st_dev:
        pytest.skip("second filesystem unavailable")
    with TemporaryDirectory(dir=volume) as temp:
        destination = Path(temp) / "models"
        change_library(str(destination), "move")
        assert models_dir() == destination and models.check_manifest("firered-vad")
        assert not (legacy / "firered-vad-ncnn/weights.bin").exists()
        shutil.rmtree(destination)
        with pytest.raises(ValueError, match="模型庫無法使用"):
            modelmanager.start_download(["firered-vad"])
