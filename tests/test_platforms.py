"""Cross-platform runtime paths, process ownership and native dialog contracts."""

from types import SimpleNamespace
import signal

import pytest

from onsei import models, processes, native_picker, environment


def test_windows_runtime_paths_select_windows_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "ROOT", tmp_path)
    monkeypatch.setattr(models.sys, "platform", "win32")
    assert models.venv_python(tmp_path).as_posix().endswith("Scripts/python.exe")
    for kind in ("vad", "whisper", "whisper-cuda", "whisper-convert"):
        assert models.runtime_lock(kind).name == kind + "-windows.lock"
    monkeypatch.setattr(models.sys, "platform", "linux")
    assert models.venv_python(tmp_path).as_posix().endswith("bin/python")
    assert models.runtime_lock("vad").name == "vad.lock"


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


def test_windows_headless_cancellation_terminates_descendants(tmp_path, monkeypatch):
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(processes.os, "kill", lambda *a: (_ for _ in ()).throw(OSError("no console")))
    calls = []
    child = SimpleNamespace(kill=lambda: calls.append("child"))
    parent = SimpleNamespace(children=lambda recursive: [child], kill=lambda: calls.append("parent"))
    monkeypatch.setattr(processes.psutil, "Process", lambda pid: parent)
    processes.signal_group(22, signal.SIGTERM)
    assert calls == ["child", "parent"]


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
        native_picker.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2, stdout="")
    )
    with pytest.raises(ValueError, match="Windows"):
        native_picker.choose_path("directory")
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
