import subprocess
import sys
import time

from filelock import FileLock

from onsei.runner import recover, run_job
from onsei.storage import Store


def test_global_lock_excludes_other_process(tmp_path):
    path = tmp_path / "gpu.lock"
    with FileLock(path):
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "from filelock import FileLock,Timeout\nimport sys\ntry:\n FileLock(sys.argv[1]).acquire(timeout=.1)\nexcept Timeout:\n sys.exit(7)",
                str(path),
            ]
        )
        assert r.returncode == 7


def test_interrupted_recovery_and_queued_cancel(tmp_path, monkeypatch):
    import onsei.runner as runner

    s = Store.create(tmp_path / "p", "test")
    j = s.new_job("transcribe", {})
    recover(s)
    assert s.job(j["id"])["status"] == "queued"
    j["created_at"] = time.time() - 10
    s.save_job(j)
    recover(s)
    assert s.job(j["id"])["status"] == "interrupted"
    j.update(status="running", pid=99999999)
    s.save_job(j)
    recover(s)
    assert s.job(j["id"])["status"] == "interrupted"
    j = s.new_job("transcribe", {})
    j.update(status="cancelled")
    s.save_job(j)
    monkeypatch.setattr(runner, "STATE", tmp_path / "state")
    # A previously cancelled job must never transition back to running.
    run_job(s, j["id"])
    assert s.job(j["id"])["status"] == "cancelled"


def test_shared_symlink_gpu_lock_still_excludes_other_processes(tmp_path):
    from onsei.util import gpu_lock_path

    original = tmp_path / "original"
    refactored = tmp_path / "refactored"
    original.mkdir()
    refactored.mkdir()
    lock_path = original / "gpu.lock"
    # A fresh original deployment may not have created the file yet.
    alias = refactored / "gpu.lock"
    alias.symlink_to(lock_path)
    assert gpu_lock_path(refactored) == lock_path
    with FileLock(lock_path):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path\nfrom filelock import FileLock,Timeout\nfrom onsei.util import gpu_lock_path\nimport sys\ntry:\n FileLock(gpu_lock_path(Path(sys.argv[1]))).acquire(timeout=.1)\nexcept Timeout:\n sys.exit(7)",
                str(refactored),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 7, result.stderr
    with FileLock(gpu_lock_path(refactored), timeout=0):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from filelock import FileLock,Timeout\nimport sys\ntry:\n FileLock(sys.argv[1]).acquire(timeout=.1)\nexcept Timeout:\n sys.exit(7)",
                str(lock_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 7, result.stderr
    assert alias.is_symlink() and alias.resolve().stat().st_ino == lock_path.stat().st_ino


def test_runner_uses_shared_gpu_lock_before_entering_pipeline(tmp_path, monkeypatch):
    import onsei.runner as runner

    original, state = tmp_path / "original", tmp_path / "state"
    original.mkdir()
    state.mkdir()
    (state / "gpu.lock").symlink_to(original / "gpu.lock")
    monkeypatch.setattr(runner, "STATE", state)
    store = Store.create(tmp_path / "project", "shared lock")
    job = store.new_job("transcribe", {})
    run_job(store, job["id"])
    # With no tracks, reaching this validation proves it acquired the symlink's
    # target and entered the pipeline rather than failing with ELOOP beforehand.
    finished = store.job(job["id"])
    assert finished["status"] == "failed"
    assert finished["message"] == "No tracks selected"
    with FileLock(original / "gpu.lock", timeout=0):
        pass


def test_gpu_lock_loop_reports_actionable_error(tmp_path):
    import pytest
    from onsei.util import gpu_lock_path

    (tmp_path / "gpu.lock").symlink_to("gpu.lock")
    with pytest.raises(ValueError, match="GPU 共用鎖路徑"):
        gpu_lock_path(tmp_path)
