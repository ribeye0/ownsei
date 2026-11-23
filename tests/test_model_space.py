"""Fail before downloads/installations when their destination disks are full."""

import errno
import json
from types import SimpleNamespace

import pytest

from onsei import diskspace, models, modelmanager as manager


@pytest.fixture
def local(tmp_path, monkeypatch):
    for module in (models, manager):
        monkeypatch.setattr(module, "ROOT", tmp_path)
        monkeypatch.setattr(module, "STATE", tmp_path / "state")
    return tmp_path


def free_space(monkeypatch, size):
    monkeypatch.setattr(diskspace.shutil, "disk_usage", lambda _: SimpleNamespace(free=size))


def test_combined_models_require_space_on_the_same_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(diskspace, "RESERVE", 0)
    free_space(monkeypatch, 100)
    diskspace.require_space([(tmp_path / "first", 60), (tmp_path / "second", 20)])
    with pytest.raises(diskspace.InsufficientSpace, match="磁碟空間不足"):
        diskspace.require_space([(tmp_path / "first", 60), (tmp_path / "second", 60)])


def test_destination_symlink_checks_its_actual_filesystem(tmp_path, monkeypatch):
    target = tmp_path / "storage"
    target.mkdir()
    link = tmp_path / "models"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    paths = []
    monkeypatch.setattr(diskspace.shutil, "disk_usage", lambda p: (paths.append(p), SimpleNamespace(free=0))[1])
    with pytest.raises(diskspace.InsufficientSpace):
        diskspace.require_space([(link / "not-downloaded-yet", 1)])
    assert paths and all(p == target for p in paths)


def test_failed_space_check_cannot_launch_jobs(local, monkeypatch):
    free_space(monkeypatch, 21 * 1024**2)
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **k: pytest.fail("download started"))
    with pytest.raises(diskspace.InsufficientSpace, match="21 MiB"):
        manager.start_download(manager.BASIC)
    assert not manager.STATE.exists()


def test_model_route_returns_actionable_storage_error(local, monkeypatch):
    from fastapi.testclient import TestClient
    from onsei.api import app

    free_space(monkeypatch, 21 * 1024**2)
    response = TestClient(app, base_url="http://127.0.0.1").post(
        "/api/models/download", json={"models": manager.BASIC}
    )
    assert response.status_code == 507
    assert "21 MiB" in response.json()["detail"]
    assert not manager.STATE.exists()


def test_valid_existing_models_and_runtimes_need_no_download_space(local, monkeypatch):
    free_space(monkeypatch, 0)
    monkeypatch.setattr(models, "check_manifest", lambda *a, **k: True)
    models.check_prepare_space(manager.BASIC)


def test_low_space_errors_explain_capacity_without_hiding_other_failures(local, monkeypatch):
    free_space(monkeypatch, 21 * 1024**2)
    assert "磁碟空間不足" in models.preparation_error(OSError(errno.ENOSPC, "No space left on device"))
    assert "21 MiB" in models.preparation_error(RuntimeError("Task error: Background writer channel closed"))
    assert models.preparation_error(RuntimeError("network failure")) == "network failure"
    free_space(monkeypatch, 20 * diskspace.GIB)
    writer = RuntimeError("Task error: Background writer channel closed")
    assert models.preparation_error(writer) == str(writer)


def test_worker_keeps_a_readable_out_of_space_failure(local, monkeypatch):
    state = {"id": "a" * 32, "status": "queued", "stage": "waiting"}
    models.atomic_json(manager.paths("index-nativelong-9b")[1], state)
    free_space(monkeypatch, 21 * 1024**2)
    monkeypatch.setattr(
        models, "prepare", lambda *a: (_ for _ in ()).throw(RuntimeError("IO Error: No space left on device (os error 28)"))
    )
    manager.worker("index-nativelong-9b", state["id"])
    saved = json.loads(manager.paths("index-nativelong-9b")[1].read_text())
    assert saved["status"] == "failed"
    assert "磁碟空間不足" in saved["error"] and "21 MiB" in saved["error"]
