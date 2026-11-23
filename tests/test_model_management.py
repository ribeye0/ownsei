"""Model jobs, progress, dependency preparation and conservative deletion."""

import json
import time
import threading

import pytest
from fastapi.testclient import TestClient
from filelock import FileLock

from onsei import models, modelmanager as manager
from onsei.storage import ConflictError
from onsei.util import atomic_json, file_hash


@pytest.fixture
def local(tmp_path, monkeypatch):
    state = tmp_path / "state"
    for module in (models, manager):
        monkeypatch.setattr(module, "ROOT", tmp_path)
        monkeypatch.setattr(module, "STATE", state)
    monkeypatch.setattr(manager, "llama_binary", lambda: tmp_path / "llama-server")
    import hashlib
    from onsei import vad_worker

    monkeypatch.setattr(
        vad_worker, "HASHES", {k: hashlib.sha256(b"weights").hexdigest() for k in vad_worker.HASHES}
    )
    return tmp_path


def install_receipt(name):
    path = models.model_dir(name)
    path.mkdir(parents=True, exist_ok=True)
    for filename in models.required_files(name):
        (path / filename).write_bytes(b"weights")
    metadata = {
        "repo": models.REGISTRY[name][0],
        "revision": models.REGISTRY[name][1],
        "sha256": {p.name: file_hash(p) for p in path.iterdir() if p.is_file()},
    }
    atomic_json(path / "onsei-model.json", metadata)
    kind = models.REGISTRY[name][2]
    if kind:
        python = models.venv_python(manager.STATE / kind)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
    return path


def job(name, **changes):
    state = {
        "id": "a" * 32,
        "model": name,
        "status": "queued",
        "stage": "waiting",
        "started_at": time.time(),
        "updated_at": time.time(),
        "pid": None,
        "bytes_done": 0,
        "bytes_total": 0,
        "files_done": 0,
        "files_total": 0,
        "error": None,
        "message": "waiting",
        **changes,
    }
    atomic_json(manager.paths(name)[1], state)
    return state


def test_hub_progress_reports_real_byte_counters_and_dynamic_totals():
    events = []
    bar = models.progress_bar(events.append)
    with bar(total=10, unit="B", desc="Reconstructing", mininterval=0) as progress:
        progress.update(4)
        progress.total = 20
        progress.refresh()
    counters = [v for v in events if "bytes_done" in v]
    assert any(v["bytes_done"] == 4 and v["bytes_total"] == 20 for v in counters)


def test_network_and_file_write_progress_are_independent_and_always_flushed():
    events = []
    bar = models.progress_bar(events.append)
    # Match snapshot_download's dual bars; network transfer has no reliable total.
    with (
        bar(total=1000, unit="B", desc="Downloading bytes", miniters=10**9) as network,
        bar(total=1000, unit="B", desc="Reconstructing", miniters=10**9) as files,
    ):
        network.update(250)
        files.update(10)
        network.update(250)
        files.total = 2000  # More files became known.
        files.refresh()
    bar.finish()
    last = events[-1]
    assert last["transfer_bytes_done"] == 500
    assert last["bytes_done"] == 10 and last["bytes_total"] == 2000
    assert "transfer_bytes_total" not in last  # Do not invent network completion %.
    count = len(events)
    network.refresh()
    files.close()
    assert len(events) == count  # Delayed cleanup cannot revert the next phase.


def test_progress_updates_do_not_depend_on_terminal_redraw(monkeypatch):
    events = []
    bar = models.progress_bar(events.append)
    clock = [0.0]
    monkeypatch.setattr(models.time, "monotonic", lambda: clock[0])
    with bar(total=1000, unit="B", desc="Downloading bytes", miniters=10**9) as network:
        for i in range(1, 4):
            clock[0] = float(i)
            network.update(100)
    assert [v["transfer_bytes_done"] for v in events if v["transfer_bytes_done"] > 0][:3] == [100, 200, 300]


def test_download_reporter_closes_before_verification_even_when_download_fails(local, monkeypatch):
    events = []
    held = []

    def snapshot(**kwargs):
        progress = kwargs["tqdm_class"](total=1000, unit="B", desc="Reconstructing")
        progress.update(100)
        held.append(progress)
        raise RuntimeError("connection lost")

    monkeypatch.setattr(models, "snapshot_download", snapshot)
    with pytest.raises(RuntimeError, match="connection lost"):
        models.download_snapshot("repo", "revision", local, ["model.gguf"], events.append)
    assert events[-1]["bytes_done"] == 100
    length = len(events)
    held[0].close()
    assert len(events) == length


def test_worker_preserves_stages_and_completion_and_failure(local, monkeypatch):
    state = job("firered-vad")
    events = []

    def prepare(name, progress):
        for stage in ("runtime", "download", "verify"):
            progress({"stage": stage, "bytes_done": 4, "bytes_total": 10})
            events.append(json.loads(manager.paths(name)[1].read_text())["stage"])
        install_receipt(name)

    monkeypatch.setattr(models, "prepare", prepare)
    manager.worker("firered-vad", state["id"])
    assert events == ["runtime", "download", "verify"]
    assert manager.read_job("firered-vad")["status"] == "completed"
    state = job("index-translate-9b")
    monkeypatch.setattr(models, "prepare", lambda *_: (_ for _ in ()).throw(RuntimeError("network failure")))
    manager.worker("index-translate-9b", state["id"])
    assert manager.read_job("index-translate-9b")["error"] == "network failure"


def test_worker_resets_phase_specific_counters_before_verification(local, monkeypatch):
    state = job("firered-vad")
    phases = []

    def prepare(name, progress):
        progress(
            {
                "stage": "download",
                "bytes_done": 100,
                "bytes_total": 100,
                "transfer_bytes_done": 80,
                "files_done": 4,
                "files_total": 4,
            }
        )
        progress({"stage": "verify", "files_done": 0, "files_total": 2})
        phases.append(json.loads(manager.paths(name)[1].read_text()))
        install_receipt(name)

    monkeypatch.setattr(models, "prepare", prepare)
    manager.worker("firered-vad", state["id"])
    assert phases[0]["stage"] == "verify"
    assert phases[0]["bytes_done"] == phases[0]["bytes_total"] == phases[0]["transfer_bytes_done"] == 0
    assert phases[0]["files_done"] == 0 and phases[0]["files_total"] == 2


def test_cancelled_bootstrap_cannot_start_and_late_progress_cannot_revive_it(local, monkeypatch):
    state = job("index-translate-9b")
    manager.cancel_download("index-translate-9b")
    monkeypatch.setattr(models, "prepare", lambda *_: pytest.fail("cancelled job started"))
    manager.worker("index-translate-9b", state["id"])
    assert manager.read_job("index-translate-9b")["status"] == "cancelled"
    state.update(status="running")
    atomic_json(manager.paths("index-translate-9b")[1], state)
    assert manager.read_job("index-translate-9b")["status"] == "cancelled"


@pytest.mark.parametrize("replacement", [False, True])
def test_running_worker_cannot_overwrite_cancelled_or_replaced_job(local, monkeypatch, replacement):
    name = "index-translate-9b"
    state = job(name)

    def prepare(_name, progress):
        progress({"stage": "download", "bytes_done": 1})
        with FileLock(manager.STATE / "model-submit.lock"):
            current = json.loads(manager.paths(name)[1].read_text())
            current.update(id="b" * 32) if replacement else current.update(status="cancelled")
            atomic_json(manager.paths(name)[1], current)
        progress({"stage": "verify", "bytes_done": 999})

    monkeypatch.setattr(models, "prepare", prepare)
    monkeypatch.setattr(manager, "installed", lambda _: True)
    manager.worker(name, state["id"])
    saved = json.loads(manager.paths(name)[1].read_text())
    assert saved["id"] == ("b" * 32 if replacement else state["id"])
    assert saved["status"] == ("running" if replacement else "cancelled")
    assert saved["bytes_done"] == 1


def test_progress_writer_excludes_catalog_reader_during_atomic_replacement(local, monkeypatch):
    name = "index-translate-9b"
    state = job(name)
    writing, release, read_done = threading.Event(), threading.Event(), threading.Event()
    failures = []
    original = manager.atomic_json

    def write(path, value):
        if value["status"] == "running" and value["stage"] == "download":
            writing.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release progress writer")
        original(path, value)

    def read():
        try:
            assert manager.read_job(name)["id"] == state["id"]
        except Exception as error:
            failures.append(error)
        finally:
            read_done.set()

    monkeypatch.setattr(manager, "atomic_json", write)
    monkeypatch.setattr(models, "prepare", lambda _, progress: progress({"stage": "download"}))
    monkeypatch.setattr(manager, "installed", lambda _: True)
    worker = threading.Thread(target=manager.worker, args=(name, state["id"]))
    reader = threading.Thread(target=read)
    worker.start()
    try:
        assert writing.wait(5)
        reader.start()
        assert not read_done.wait(0.1), "Catalog read raced the progress writer"
    finally:
        release.set()
        worker.join(timeout=5)
        if reader.ident is not None:
            reader.join(timeout=5)
    assert not worker.is_alive() and not reader.is_alive()
    assert not failures
    assert manager.read_job(name)["status"] == "completed"


@pytest.mark.parametrize("status", ["queued", "running", "failed", "completed"])
def test_catalog_does_not_report_ready_before_preparation_finishes(local, monkeypatch, status):
    name = "index-translate-9b"
    monkeypatch.setattr(manager, "installed", lambda candidate: candidate == name)
    job(name, status=status)
    row = next(row for row in manager.catalog()["models"] if row["id"] == name)
    assert row["installed"]
    assert row["ready"] == (status == "completed")


def test_dead_worker_marked_interrupted_and_invalid_identity_rejected(local):
    job("index-translate-9b", status="running", pid=99999999, process_identity="no-such-process")
    assert manager.read_job("index-translate-9b")["status"] == "interrupted"
    with pytest.raises(ValueError):
        manager.read_job("../outside")


def test_delete_removes_only_model_and_its_source_and_preserves_runtime_and_works(local):
    path = install_receipt("firered-vad")
    source = local / "models/.sources/firered-vad"
    source.mkdir(parents=True)
    (source / "model.bin").write_bytes(b"raw")
    work = local / "workspace/data"
    work.mkdir(parents=True)
    (work / "original.wav").write_bytes(b"audio")
    job("firered-vad", status="completed")
    result = manager.delete_model("firered-vad")
    assert result["removed_bytes"] > 0 and not path.exists() and not source.exists()
    assert manager.read_job("firered-vad")["status"] == "deleted"
    assert (
        models.venv_python(manager.STATE / "vad").exists()
        and (work / "original.wav").read_bytes() == b"audio"
    )


@pytest.mark.parametrize("lock", ["gpu.lock", "model-operation.lock"])
def test_delete_refuses_busy_and_symlink_paths(local, lock):
    path = install_receipt("index-translate-9b")
    with FileLock(manager.STATE / lock):
        with pytest.raises(ConflictError):
            manager.delete_model("index-translate-9b")
    assert path.exists()
    manager.delete_model("index-translate-9b")
    external = local / "external"
    external.mkdir()
    (external / "keep").touch()
    path.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError):
        manager.delete_model("index-translate-9b")
    assert (external / "keep").exists()


def test_queued_preparation_blocks_delete(local):
    path = install_receipt("index-translate-9b")
    job("index-translate-9b")
    with pytest.raises(ConflictError):
        manager.delete_model("index-translate-9b")
    assert path.exists()


def test_runtime_lock_receipt_reuses_pinned_environment_without_network(local, monkeypatch):
    kind = "vad"
    uv = local / ".tools/uv"
    uv.parent.mkdir()
    uv.touch()
    lock = local / f"runtimes/{kind}.lock"
    lock.parent.mkdir()
    lock.write_text("pinned==1")
    env = manager.STATE / kind
    (env / "bin").mkdir(parents=True)
    (env / "bin/python").touch()
    atomic_json(env / "onsei-runtime.json", {"lock_sha256": file_hash(lock), "python": "3.12"})
    monkeypatch.setattr(
        models.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("unchanged environment touched")
    )
    models.setup_runtime(kind)


def test_model_routes_and_same_origin_guard(local, monkeypatch):
    import onsei.api as api

    client = TestClient(api.app, base_url="http://127.0.0.1")
    assert client.get("/api/models").status_code == 200
    assert client.post("/api/models/download", json={"models": ["unknown"]}).status_code == 422
    assert client.delete("/api/models/unknown").status_code == 422
    assert (
        client.post(
            "/api/models/download",
            json={"models": ["index-translate-9b"]},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    install_receipt("index-translate-9b")
    assert client.delete("/api/models/index-translate-9b").status_code == 200
    assert client.get("/api/models/index-translate-9b/log").json() == {"text": ""}


def test_full_log_download_is_untruncated_and_view_strips_terminal_controls(local):
    import onsei.api as api

    state = job("index-translate-9b", status="completed")
    file = manager.paths("index-translate-9b")[0] / (state["id"] + ".log")
    content = "first line\n" + "x" * 210000 + "\x1b[1A\rprogress complete\n"
    file.write_text(content)
    client = TestClient(api.app, base_url="http://127.0.0.1")
    view = client.get("/api/models/index-translate-9b/log").json()["text"]
    assert "first line" not in view and "\x1b" not in view and "progress complete" in view
    full = client.get("/api/models/index-translate-9b/log/download")
    assert full.status_code == 200 and full.text == content


def test_partial_runtime_is_never_advertised_as_ready(local):
    install_receipt("firered-vad")
    atomic_json(manager.STATE / "vad/onsei-runtime-installing.json", {"lock_sha256": "unfinished"})
    model = next(m for m in manager.catalog()["models"] if m["id"] == "firered-vad")
    assert model["installed"] and not model["runtime_ready"] and not model["ready"]


def test_model_delete_respects_original_lock_through_shared_symlink(local, monkeypatch):
    original = local / "original"
    original.mkdir()
    manager.STATE.mkdir(parents=True, exist_ok=True)
    (manager.STATE / "gpu.lock").symlink_to(original / "gpu.lock")
    path = install_receipt("firered-vad")
    monkeypatch.setattr(manager, "tasks_pending", lambda: False)
    with FileLock(original / "gpu.lock"):
        with pytest.raises(ConflictError):
            manager.delete_model("firered-vad")
    assert path.exists()


def test_catalog_and_preparation_share_receipt_validation(local, monkeypatch):
    name = "index-translate-9b"
    path = install_receipt(name)
    # Fast inventory cannot treat an arbitrary receipt hash as the pinned model.
    assert not manager.installed(name)
    receipt = json.loads((path / "onsei-model.json").read_text())
    receipt["sha256"][models.GGUF[name]] = models.GGUF_SHA256[name]
    atomic_json(path / "onsei-model.json", receipt)
    assert manager.installed(name)
    assert not models.check_manifest(name)
    # Polling must never hash multi-GB files, even after refreshing the catalog.
    monkeypatch.setattr(models, "file_hash", lambda _: pytest.fail("inventory reread weights"))
    assert manager.installed(name)
    for invalid in [[], None, {**receipt, "sha256": []}, {**receipt, "sha256": {"../escape": "a"}}]:
        atomic_json(path / "onsei-model.json", invalid)
        assert not manager.installed(name)
