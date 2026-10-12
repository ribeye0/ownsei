"""Model jobs, progress, dependency preparation and conservative deletion."""

import json
import time

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
    return tmp_path


def install_receipt(name):
    path = models.model_dir(name)
    path.mkdir(parents=True, exist_ok=True)
    filename = models.GGUF.get(name, "model.bin" if name in models.WHISPER_MODELS else "model.safetensors")
    (path / filename).write_bytes(b"weights")
    if name not in models.GGUF:
        (path / "config.json").write_text("{}")
        (path / "tokenizer.json").write_text("{}")
    metadata = {
        "repo": models.REGISTRY[name][0],
        "revision": models.REGISTRY[name][1],
        "sha256": {filename: file_hash(path / filename)},
    }
    if name in models.WHISPER_CONVERSIONS:
        metadata["conversion"] = models.WHISPER_CONVERSION_CONFIG
    atomic_json(path / "onsei-model.json", metadata)
    kind = models.REGISTRY[name][2]
    if kind:
        python = manager.STATE / kind / "bin/python"
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


def test_catalog_uses_pinned_sources_and_distinguishes_runtime_and_dependencies(local):
    install_receipt("whisper-ja-760m")
    install_receipt("cohere-asr-ja")
    loader = local / ".tools/cohere-source/cohere_asr.py"
    loader.parent.mkdir(parents=True)
    loader.touch()
    entries = {v["id"]: v for v in manager.catalog()["models"]}
    assert entries["whisper-ja-760m"]["ready"]
    assert not entries["cohere-asr-ja"]["ready"] and entries["cohere-asr-ja"]["dependencies"] == ["aligner"]
    install_receipt("aligner")
    assert next(v for v in manager.catalog()["models"] if v["id"] == "cohere-asr-ja")["ready"]
    path = models.model_dir("whisper-ja-760m")
    (path / "tokenizer.json").unlink()
    assert not manager.installed("whisper-ja-760m")
    assert manager.catalog()["basic"] == ["whisper-ja-760m", "index-translate-9b"]


def test_hub_progress_reports_real_byte_counters_and_dynamic_totals():
    events = []
    bar = models.progress_bar(events.append)
    with bar(total=10, unit="B", desc="Reconstructing", mininterval=0) as progress:
        progress.update(4)
        progress.total = 20
        progress.refresh()
    counters = [v for v in events if "bytes_done" in v]
    assert any(v["bytes_done"] == 4 and v["bytes_total"] == 20 for v in counters)


def test_worker_preserves_stages_and_completion_and_failure(local, monkeypatch):
    state = job("whisper-ja-760m")
    events = []

    def prepare(name, progress):
        for stage in ("runtime", "download", "verify"):
            progress({"stage": stage, "bytes_done": 4, "bytes_total": 10})
            events.append(json.loads(manager.paths(name)[1].read_text())["stage"])
        install_receipt(name)

    monkeypatch.setattr(models, "prepare", prepare)
    manager.worker("whisper-ja-760m", state["id"])
    assert events == ["runtime", "download", "verify"]
    assert manager.read_job("whisper-ja-760m")["status"] == "completed"
    state = job("index-translate-2b")
    monkeypatch.setattr(models, "prepare", lambda *_: (_ for _ in ()).throw(RuntimeError("network failure")))
    manager.worker("index-translate-2b", state["id"])
    assert manager.read_job("index-translate-2b")["error"] == "network failure"


def test_downloads_add_cohere_dependency_and_do_not_launch_duplicates(local, monkeypatch):
    launched = []

    class Process:
        def __init__(self, args, **kwargs):
            launched.append(args)

        def wait(self):
            return 0

    monkeypatch.setattr(manager.subprocess, "Popen", Process)
    states = manager.start_download(["cohere-asr-ja", "cohere-asr-ja"])
    assert [s["model"] for s in states] == ["aligner", "cohere-asr-ja"] and len(launched) == 2
    manager.start_download(["cohere-asr-ja"])
    assert len(launched) == 2
    with pytest.raises(ValueError):
        manager.start_download(["../../outside"])


def test_cancelled_bootstrap_cannot_start_and_late_progress_cannot_revive_it(local, monkeypatch):
    state = job("index-translate-2b")
    manager.cancel_download("index-translate-2b")
    monkeypatch.setattr(models, "prepare", lambda *_: pytest.fail("cancelled job started"))
    manager.worker("index-translate-2b", state["id"])
    assert manager.read_job("index-translate-2b")["status"] == "cancelled"
    state.update(status="running")
    atomic_json(manager.paths("index-translate-2b")[1], state)
    assert manager.read_job("index-translate-2b")["status"] == "cancelled"


def test_dead_worker_marked_interrupted_and_invalid_identity_rejected(local):
    job("index-translate-2b", status="running", pid=99999999, process_identity="no-such-process")
    assert manager.read_job("index-translate-2b")["status"] == "interrupted"
    with pytest.raises(ValueError):
        manager.read_job("../outside")


def test_delete_removes_only_model_and_its_source_and_preserves_runtime_and_works(local):
    path = install_receipt("whisper-ja-760m")
    source = local / "models/.sources/whisper-ja-760m"
    source.mkdir(parents=True)
    (source / "model.bin").write_bytes(b"raw")
    work = local / "workspace/data"
    work.mkdir(parents=True)
    (work / "original.wav").write_bytes(b"audio")
    job("whisper-ja-760m", status="completed")
    result = manager.delete_model("whisper-ja-760m")
    assert result["removed_bytes"] > 0 and not path.exists() and not source.exists()
    assert manager.read_job("whisper-ja-760m")["status"] == "deleted"
    assert (manager.STATE / "whisper/bin/python").exists() and (
        work / "original.wav"
    ).read_bytes() == b"audio"


@pytest.mark.parametrize("lock", ["gpu.lock", "model-operation.lock"])
def test_delete_refuses_busy_and_symlink_paths(local, lock):
    path = install_receipt("index-translate-2b")
    with FileLock(manager.STATE / lock):
        with pytest.raises(ConflictError):
            manager.delete_model("index-translate-2b")
    assert path.exists()
    manager.delete_model("index-translate-2b")
    external = local / "external"
    external.mkdir()
    (external / "keep").touch()
    path.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError):
        manager.delete_model("index-translate-2b")
    assert (external / "keep").exists()


def test_queued_preparation_blocks_delete(local):
    path = install_receipt("index-translate-2b")
    job("index-translate-2b")
    with pytest.raises(ConflictError):
        manager.delete_model("index-translate-2b")
    assert path.exists()


def test_runtime_lock_receipt_reuses_pinned_environment_without_network(local, monkeypatch):
    kind = "whisper"
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
            json={"models": ["index-translate-2b"]},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    install_receipt("index-translate-2b")
    assert client.delete("/api/models/index-translate-2b").status_code == 200
    assert client.get("/api/models/index-translate-2b/log").json() == {"text": ""}


def test_corrupt_receipt_triggers_repair_not_reuse(local, monkeypatch):
    path = install_receipt("hy-mt2-1.8b")
    (path / models.GGUF["hy-mt2-1.8b"]).write_bytes(b"corrupt")
    fetched = []

    def download(**kwargs):
        fetched.append(kwargs)
        (path / models.GGUF["hy-mt2-1.8b"]).write_bytes(b"repaired")

    monkeypatch.setattr(models, "snapshot_download", download)
    assert models.prepare("hy-mt2-1.8b") == path
    assert fetched[0]["force_download"] is True
    assert models.check_manifest("hy-mt2-1.8b")


def test_full_log_download_is_untruncated_and_view_strips_terminal_controls(local):
    import onsei.api as api

    state = job("index-translate-2b", status="completed")
    file = manager.paths("index-translate-2b")[0] / (state["id"] + ".log")
    content = "first line\n" + "x" * 210000 + "\x1b[1A\rprogress complete\n"
    file.write_text(content)
    client = TestClient(api.app, base_url="http://127.0.0.1")
    view = client.get("/api/models/index-translate-2b/log").json()["text"]
    assert "first line" not in view and "\x1b" not in view and "progress complete" in view
    full = client.get("/api/models/index-translate-2b/log/download")
    assert full.status_code == 200 and full.text == content


def test_partial_runtime_is_never_advertised_as_ready(local):
    install_receipt("whisper-ja-760m")
    atomic_json(manager.STATE / "whisper/onsei-runtime-installing.json", {"lock_sha256": "unfinished"})
    model = next(m for m in manager.catalog()["models"] if m["id"] == "whisper-ja-760m")
    assert model["installed"] and not model["runtime_ready"] and not model["ready"]
