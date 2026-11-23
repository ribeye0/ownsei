import os
import signal
import subprocess
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from onsei.audio import import_audio, pcm, playback_source
from onsei.domain import Cue
from onsei.export import export_track
from onsei.snapshots import fresh_exports
from onsei.storage import Store, ConflictError


def wav(path, seconds=1):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000 * seconds)


@pytest.fixture
def result(tmp_path):
    source = tmp_path / "fixture.wav"
    wav(source)
    store = Store.create(tmp_path / "project", "穩定化測試")
    track = import_audio(store, source)[0]
    store.replace_cues(
        track,
        "v",
        [
            Cue(
                id="c",
                track_id=track.id,
                version="v",
                ordinal=0,
                start_ms=0,
                end_ms=900,
                ja_raw="はい",
                zh_auto="好",
                translation_status="translated",
            )
        ],
    )
    store.translation_versions(track.id)
    return store, track, source


def test_concurrent_edits_accept_only_one_snapshot(result):
    store, _, _ = result
    before = store.cue("c")
    barrier = threading.Barrier(2)

    def edit(text):
        barrier.wait(timeout=5)
        try:
            return store.edit("c", {"zh_override": text}, before.revision).zh
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        answers = list(pool.map(edit, ["甲", "乙"]))
    assert answers.count("conflict") == 1
    assert store.cue("c").revision == before.revision + 1
    assert store.cue("c").zh == next(x for x in answers if x != "conflict")


def test_generated_write_invalidates_open_editor_and_stale_inference(result):
    store, _, _ = result
    opened = store.cue("c")
    generated = opened.model_copy(deep=True)
    generated.zh_auto = "更新"
    assert store.save_generated(generated, opened.revision)
    with pytest.raises(ConflictError):
        store.edit("c", {"zh_override": "舊草稿"}, opened.revision)
    fresh = store.cue("c")
    store.edit("c", {"zh_override": "校對"}, fresh.revision)
    assert not store.save_generated(generated, generated.revision)
    assert store.cue("c").zh == "校對"


def test_api_conflict_and_wrong_result_keep_stored_text(result, tmp_path, monkeypatch):
    import onsei.api as api

    store, track, _ = result
    monkeypatch.setattr(api, "REGISTRY", tmp_path / "index.db")
    client = TestClient(api.app, base_url="http://127.0.0.1")
    p = client.post("/api/projects", json={"title": "fixture", "path": str(store.path)}).json()["id"]
    url = f"/api/projects/{p}/cues/c"
    before = store.cue("c")
    body = {
        "revision": before.revision,
        "translation_version": "another-result",
        "changes": {"zh_override": "錯誤結果"},
    }
    assert client.patch(url, json=body).status_code == 409
    body["translation_version"] = before.provenance["translation_version"]
    assert client.patch(url, json=body).status_code == 200
    assert client.patch(url, json=body).status_code == 409
    assert store.cue("c").zh == "錯誤結果"


def test_replaced_source_blocks_cached_decode_and_playback(result):
    store, track, source = result
    cached = pcm(store, track)
    original = cached.read_bytes()
    wav(source, seconds=2)
    for access in (pcm, playback_source):
        with pytest.raises(ConflictError, match="重新匯入"):
            access(store, store.track(track.id))
    assert cached.read_bytes() == original
    assert store.cue("c").ja == "はい"
    imported = import_audio(store, source)
    assert len(imported) == 1 and imported[0].id != track.id


def test_api_source_replacement_returns_conflict(result, tmp_path, monkeypatch):
    import onsei.api as api

    store, track, source = result
    monkeypatch.setattr(api, "REGISTRY", tmp_path / "index.db")
    client = TestClient(api.app, base_url="http://127.0.0.1")
    p = client.post("/api/projects", json={"title": "fixture", "path": str(store.path)}).json()["id"]
    wav(source, seconds=2)
    response = client.get(f"/api/projects/{p}/tracks/{track.id}/audio")
    assert response.status_code == 409
    assert "重新匯入" in response.json()["detail"]


def test_old_active_job_is_present_in_details_and_poll(result, tmp_path, monkeypatch):
    import onsei.api as api
    from onsei.processes import process_identity

    store, _, _ = result
    monkeypatch.setattr(api, "REGISTRY", tmp_path / "index.db")
    old = store.new_job("transcribe", {})
    old.update(status="running", pid=os.getpid(), process_identity=process_identity(os.getpid()))
    store.save_job(old)
    for _ in range(55):
        job = store.new_job("export", {})
        job.update(status="completed")
        store.save_job(job)
    client = TestClient(api.app, base_url="http://127.0.0.1")
    p = client.post("/api/projects", json={"title": "fixture", "path": str(store.path)}).json()["id"]
    for jobs in (
        client.get(f"/api/projects/{p}").json()["jobs"],
        client.get(f"/api/projects/{p}/jobs").json(),
    ):
        assert any(j["id"] == old["id"] and j["status"] == "running" for j in jobs)


def test_legacy_source_identity_and_touch_without_change(result):
    from onsei.source import validate_source

    store, track, source = result
    track.source_signature = None
    store.save_track(track)
    assert validate_source(store, track) == store.track(track.id).source_signature
    cached = pcm(store, track)
    timestamp = cached.stat().st_mtime_ns
    os.utime(source, None)
    assert pcm(store, store.track(track.id)) == cached
    assert cached.stat().st_mtime_ns == timestamp


def test_corrupt_pcm_is_preserved_then_rebuilt(result):
    store, track, _ = result
    cached = pcm(store, track)
    cached.write_bytes(b"corrupt")
    assert pcm(store, track) == cached
    with wave.open(str(cached)) as audio:
        assert audio.getnframes() == 16000
    assert list(cached.parent.glob("*.invalid"))


def test_concurrent_exports_publish_complete_files(result):
    store, track, _ = result
    barrier = threading.Barrier(4)

    def export(_):
        barrier.wait(timeout=5)
        return export_track(store, track.id, store.path / "exports")

    with ThreadPoolExecutor(4) as pool:
        outputs = list(pool.map(export, range(4)))
    assert all(len(paths) == 16 for paths in outputs)
    assert len(fresh_exports(store)) == 16
    assert not list((store.path / "exports").glob(".export-*"))


def test_export_rejects_edit_during_render(result, monkeypatch):
    import onsei.export as exports

    store, track, _ = result
    render = exports._render

    def edit_during_render(*args):
        render(*args)
        store.edit("c", {"zh_override": "更新"}, store.cue("c").revision)

    monkeypatch.setattr(exports, "_render", edit_during_render)
    with pytest.raises(ConflictError, match="匯出期間"):
        export_track(store, track.id, store.path / "exports")
    assert fresh_exports(store) == []
    assert not list((store.path / "exports").glob("*.srt"))


def test_publication_failure_removes_old_manifest(result, monkeypatch):
    store, track, _ = result
    export_track(store, track.id, store.path / "exports")
    replace = Path.replace

    def fail(path, target):
        if ".export-" in str(path) and path.name.endswith(".srt"):
            raise OSError("disk failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        export_track(store, track.id, store.path / "exports")
    assert fresh_exports(store) == []


def test_reused_pid_is_not_a_live_job_and_cannot_receive_signal(result, monkeypatch):
    import onsei.runner as runner
    from onsei.processes import process_identity

    store, _, _ = result
    job = store.new_job("translate", {})
    job.update(
        status="running", pid=os.getpid(), process_identity=process_identity(os.getpid()) + "different"
    )
    store.save_job(job)
    signals = []
    monkeypatch.setattr(runner.os, "killpg", lambda *args: signals.append(args))
    runner.recover(store)
    assert store.job(job["id"])["status"] == "interrupted"
    runner.cancel_job(store, job["id"])
    assert signals == []


def test_worker_cannot_undo_cancel_request(result):
    store, _, _ = result
    old = store.new_job("translate", {})
    current = dict(old, status="cancelling", cancel_requested_at=time.time())
    store.save_job(current)
    old.update(status="running")
    store.save_job(old)
    assert store.job(old["id"])["status"] == "cancelling"
    assert store.active_jobs()[0]["id"] == old["id"]


def test_resume_export_failure_skips_translation_but_not_changed_input(result, tmp_path, monkeypatch):
    import onsei.runner as runner
    import onsei.translation_pipeline as pipeline
    import onsei.export as exports

    store, track, _ = result

    class Metrics:
        data = {}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(runner, "GpuMetrics", Metrics)
    monkeypatch.setattr(runner, "STATE", tmp_path / "gpu")
    calls = []
    monkeypatch.setattr(pipeline, "process_translation", lambda *args, **kwargs: calls.append("translation"))
    real_export = exports.export_track

    def fail(*args, **kwargs):
        raise ValueError("export blocked")

    monkeypatch.setattr(exports, "export_track", fail)
    job = store.new_job("translate", {"track_ids": [track.id]})
    runner.run_job(store, job["id"])
    failed = store.job(job["id"])
    assert failed["status"] == "partial" and failed["failed_stage"] == "export"
    assert failed["stages"][track.id]["translate"]["status"] == "completed"
    monkeypatch.setattr(exports, "export_track", real_export)
    retry = runner.resume_job(store, failed)
    runner.run_job(store, retry["id"])
    assert store.job(retry["id"])["status"] == "completed"
    assert calls == ["translation"]
    store.edit("c", {"zh_override": "人工更新"}, store.cue("c").revision)
    retry = runner.resume_job(store, failed)
    runner.run_job(store, retry["id"])
    assert calls == ["translation", "translation"]


def test_waiting_runner_cancel_confirms_exit_and_keeps_gpu_lock(result, tmp_path, monkeypatch):
    from filelock import FileLock
    import onsei.runner as runner
    from onsei.processes import alive

    store, track, _ = result
    root = tmp_path / "runtime"
    monkeypatch.setenv("ONSEI_ROOT", str(root))
    locks = root / ".runtime-envs"
    locks.mkdir(parents=True)
    job = store.new_job("transcribe", {"track_ids": [track.id]})
    processes = []
    spawn = runner.subprocess.Popen

    def capture(*args, **kwargs):
        child = spawn(*args, **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", capture)
    with FileLock(locks / "gpu.lock"):
        runner.start_job(store, job["id"])
        child = processes[0]
        try:
            deadline = time.monotonic() + 8
            while store.job(job["id"])["status"] != "waiting" and time.monotonic() < deadline:
                time.sleep(0.02)
            assert store.job(job["id"])["status"] == "waiting"
            runner.cancel_job(store, job["id"])
            assert store.job(job["id"])["status"] in ("cancelling", "cancelled")
            child.wait(timeout=8)
            runner.recover(store)
            assert store.job(job["id"])["status"] == "cancelled"
            assert not alive(store.job(job["id"]))
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)


@pytest.mark.parametrize("closed_stdout", [False, True])
def test_silent_worker_times_out_and_is_reaped(tmp_path, monkeypatch, closed_stdout):
    import onsei.asr as asr

    monkeypatch.setenv("ONSEI_WORKER_IDLE_TIMEOUT", ".05")
    monkeypatch.setattr(asr, "runtime_python", lambda _: Path(sys.executable))
    monkeypatch.setattr(asr, "worker_env", lambda _: dict(os.environ))
    spawn = subprocess.Popen
    children = []

    def silent(*args, **kwargs):
        code = "import os,time; " + ("os.close(1); " if closed_stdout else "") + "time.sleep(20)"
        child = spawn([sys.executable, "-c", code], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asr.subprocess, "Popen", silent)
    with pytest.raises(RuntimeError, match="仍未退出" if closed_stdout else "沒有進展"):
        asr.invoke(
            "whisper",
            tmp_path,
            tmp_path / "audio.wav",
            [{"output": str(tmp_path / "checkpoint.json")}],
            tmp_path,
            lambda _: None,
        )
    assert children[0].poll() is not None
    assert not (tmp_path / "checkpoint.json").exists()


def test_shutdown_escalates_for_ignored_termination():
    from onsei.processes import stop_process

    child = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(20)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        stop_process(child, timeout=0.05)
        assert child.poll() == -signal.SIGKILL
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_corrupt_worker_checkpoint_is_quarantined_and_recomputed(tmp_path, monkeypatch):
    import json
    import onsei.asr as asr

    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("{broken")
    children = []

    class Worker:
        stdout = iter([])

        def __init__(self, *args, **kwargs):
            children.append(self)
            checkpoint.write_text(json.dumps({"data": []}))

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(asr.subprocess, "Popen", Worker)
    monkeypatch.setattr(asr, "runtime_python", lambda _: Path("/fake"))
    monkeypatch.setattr(asr, "worker_env", lambda _: {})
    asr.invoke(
        "whisper", tmp_path, tmp_path / "audio.wav", [{"output": str(checkpoint)}], tmp_path, lambda _: None
    )
    assert len(children) == 1
    assert json.loads(checkpoint.read_text())["data"] == []
    assert list(tmp_path.glob("*.invalid.json"))
