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
