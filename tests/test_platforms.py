"""Cross-platform runtime paths, process ownership and native dialog contracts."""

from types import SimpleNamespace
import signal

import pytest

from onsei import models, processes, native_picker, environment


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_vad_requires_both_native_extensions_for_this_platform(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(models, "ROOT", tmp_path)
    monkeypatch.setattr(models.sys, "platform", platform)
    folder = tmp_path / "bin/vad"
    for lib in models.vad_libraries("windows" if platform == "win32" else "linux"):
        assert not models.vad_runtime_ready()
        path = folder / lib
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert models.vad_runtime_ready()


def test_windows_process_identity_never_authorizes_reused_or_legacy_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(processes.psutil, "pid_exists", lambda pid: True)
    process = SimpleNamespace(
        create_time=lambda: 123.5,
        cmdline=lambda: ["python", "onsei.runner", str(tmp_path), "job"],
        cwd=lambda: str(tmp_path),
    )
    monkeypatch.setattr(processes.psutil, "Process", lambda pid: process)
    job = {"pid": 22, "id": "job", "process_identity": "win:123.5"}
    assert processes.process_identity(22) == "win:123.5"
    assert processes.runner_owned(job, tmp_path)
    assert processes.process_owned(job, ["onsei.runner"], tmp_path)
    assert not processes.process_owned(job, ["onsei.runner"], tmp_path / "other")
    assert not processes.runner_owned({**job, "process_identity": "win:123.6"}, tmp_path)
    assert not processes.runner_owned({**job, "process_identity": None}, tmp_path)
    assert not processes.runner_owned({**job, "id": "other"}, tmp_path)
    assert processes.spawn_options() == {"creationflags": 0x200}


def test_windows_cancellation_targets_only_the_worker_tree(monkeypatch):
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(
        processes.os, "kill", lambda *a: pytest.fail("Cancellation must not signal the console")
    )
    calls = []
    child = SimpleNamespace(kill=lambda: calls.append("child"))
    parent = SimpleNamespace(children=lambda recursive: [child], kill=lambda: calls.append("parent"))
    monkeypatch.setattr(processes.psutil, "Process", lambda pid: parent)
    processes.signal_group(22, signal.SIGTERM)
    assert calls == ["child", "parent"]


def test_windows_runner_cancel_is_cooperative_then_reaps_only_owned_worker(tmp_path, monkeypatch):
    from onsei import runner
    from onsei.storage import Store

    monkeypatch.setattr(runner.sys, "platform", "win32")
    monkeypatch.setattr(runner, "alive", lambda _: True)
    monkeypatch.setattr(runner, "runner_owned", lambda *_: True)
    signals = []
    monkeypatch.setattr(runner, "signal_group", lambda *args: signals.append(args))
    store = Store.create(tmp_path / "project", "cancel")
    job = store.new_job("transcribe", {})
    job.update(status="running", pid=1234, process_identity="owned-test-process")
    store.save_job(job)
    runner.cancel_job(store, job["id"])
    assert store.job(job["id"])["status"] == "cancelling"
    assert signals == []  # Never send a console event to a venv redirector PID.
    job = store.job(job["id"])
    monkeypatch.setattr(runner.time, "time", lambda: job["cancel_requested_at"] + 11)
    monkeypatch.setattr(runner, "runner_owned", lambda *_: False)
    runner.recover(store)
    assert signals == []
    monkeypatch.setattr(runner, "runner_owned", lambda *_: True)
    runner.recover(store)
    assert signals == [(1234, getattr(signal, "SIGKILL", 9))]


def test_windows_dialog_uses_literal_arguments_and_preserves_filename(tmp_path, monkeypatch):
    source = tmp_path / "空格 中文 & $(echo test).wav"
    source.touch()
    monkeypatch.setattr(native_picker.sys, "platform", "win32")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=str(source) + "\r\n")

    monkeypatch.setattr(native_picker.subprocess, "run", run)
    assert native_picker.choose_path("file", str(tmp_path)) == str(source)
    args, kwargs = calls[0]
    assert args[-2:] == ["-Initial", str(tmp_path)]
    assert args[args.index("-File") + 1].endswith("choose_path.ps1")
    assert not kwargs.get("shell")
    assert kwargs["encoding"] == "utf-8"
    monkeypatch.setattr(
        native_picker.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="")
    )
    assert native_picker.choose_path("directory") is None
    monkeypatch.setattr(
        native_picker.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="picker failed"),
    )
    with pytest.raises(ValueError, match="Windows"):
        native_picker.choose_path("directory")
    assert native_picker._dialog.acquire(blocking=False)
    native_picker._dialog.release()


def test_windows_dialog_omits_empty_initial_argument_and_reports_error(monkeypatch, caplog):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1, stdout="", stderr="WinForms unavailable\n" + "x" * 3000)

    monkeypatch.setattr(native_picker.subprocess, "run", run)
    with pytest.raises(ValueError, match="WinForms unavailable") as failure:
        native_picker.choose_windows_path("file", "")
    args, kwargs = calls[0]
    assert "-Initial" not in args
    assert "-STA" in args
    assert kwargs["errors"] == "replace"
    assert len(str(failure.value)) < 2100
    assert "exited 1" in caplog.text
    assert native_picker._dialog.acquire(blocking=False)
    native_picker._dialog.release()


@pytest.mark.parametrize(
    "failure, message",
    [
        (FileNotFoundError("missing"), "powershell.exe"),
        (native_picker.subprocess.TimeoutExpired("powershell.exe", 300), "逾時"),
    ],
)
def test_windows_dialog_launch_failure_releases_lock(monkeypatch, failure, message):
    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(native_picker.subprocess, "run", run)
    with pytest.raises(ValueError, match=message):
        native_picker.choose_windows_path("file", "")
    assert native_picker._dialog.acquire(blocking=False)
    native_picker._dialog.release()


def test_native_windows_process_identity_is_live():
    # Also runs unmocked on the Windows CI runner.
    import os

    identity = processes.process_identity(os.getpid())
    assert identity
    assert processes.alive({"pid": os.getpid(), "process_identity": identity})
    assert not processes.alive({"pid": os.getpid(), "process_identity": identity + "other"})


def test_windows_readiness_does_not_require_glibc(tmp_path, monkeypatch):
    monkeypatch.setattr(environment.platform, "system", lambda: "Windows")
    monkeypatch.setattr(environment.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(environment, "ROOT", tmp_path)
    monkeypatch.setattr(environment.shutil, "which", lambda name: name)
    monkeypatch.setattr(environment, "command", lambda *a, **k: (True, "available"))
    report = environment.inspect_environment(engines=False)
    assert report["ready"]
    assert "glibc" not in {c["name"] for c in report["checks"]}


def test_doctor_handles_cpu_only_engine_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(environment, "ROOT", tmp_path)
    monkeypatch.setattr(environment, "command", lambda *args, **kwargs: (True, "available"))
    monkeypatch.setattr(environment.backend, "devices", lambda _: [{"id": "CPU", "name": "CPU"}])

    checks = {item["name"]: item for item in environment.inspect_environment()["checks"]}
    assert checks["speech"]["ok"] and checks["llama"]["ok"]
    assert not checks["vulkan_gpu"]["ok"] and not checks["vulkan_gpu"]["required"]
