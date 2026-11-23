from types import SimpleNamespace
import subprocess

import pytest
from fastapi.testclient import TestClient

from onsei import api, native_picker


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "niri")
    monkeypatch.setattr(
        native_picker.shutil, "which", lambda name: "/usr/bin/" + name if name == "zenity" else None
    )


def test_native_file_path_preserves_unicode_spaces_and_shell_characters(tmp_path, monkeypatch, desktop):
    source = tmp_path / "01：テスト $(touch no) `x` .WAV"
    source.touch()
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=str(source) + "\n")

    monkeypatch.setattr(native_picker.subprocess, "run", run)
    assert native_picker.choose_path("file", str(tmp_path)) == str(source)
    args, kwargs = calls[0]
    assert args[0] == "/usr/bin/zenity"
    assert "--filename=" + str(tmp_path) + "/" in args
    assert not kwargs.get("shell")
    assert any("*.WAV" in arg for arg in args)


def test_native_cancel_failure_timeout_and_lock_release(monkeypatch, desktop):
    monkeypatch.setattr(
        native_picker.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="")
    )
    assert native_picker.choose_path("directory") is None
    monkeypatch.setattr(
        native_picker.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2, stdout="")
    )
    with pytest.raises(ValueError, match="無法開啟"):
        native_picker.choose_path("directory")

    def timed_out(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 300)

    monkeypatch.setattr(native_picker.subprocess, "run", timed_out)
    with pytest.raises(ValueError, match="逾時"):
        native_picker.choose_path("file")
    assert native_picker._dialog.acquire(blocking=False)
    native_picker._dialog.release()
    native_picker._dialog.acquire()
    try:
        with pytest.raises(ValueError, match="已開啟"):
            native_picker.choose_path("directory")
    finally:
        native_picker._dialog.release()


def test_native_kde_folder_and_headless_fallback(tmp_path, monkeypatch, desktop):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(native_picker.shutil, "which", lambda name: "/usr/bin/" + name)
    args = []
    monkeypatch.setattr(
        native_picker.subprocess,
        "run",
        lambda command, **kw: (
            args.extend(command),
            SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n"),
        )[1],
    )
    assert native_picker.choose_path("directory") == str(tmp_path)
    assert args[0] == "/usr/bin/kdialog" and "--getexistingdirectory" in args
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(ValueError, match="桌面"):
        native_picker.choose_path("file")


def test_picker_api_validates_scope_and_cancel(monkeypatch):
    calls = []
    monkeypatch.setattr(
        native_picker, "choose_path", lambda kind, initial: (calls.append((kind, initial)), None)[1]
    )
    client = TestClient(api.app, base_url="http://127.0.0.1")
    assert client.post("/api/native-picker", json={"kind": "file", "initial": "/music"}).json() == {
        "path": None
    }
    assert calls == [("file", "/music")]
    assert client.post("/api/native-picker", json={"kind": "script"}).status_code == 422
    assert (
        client.post(
            "/api/native-picker", json={"kind": "file"}, headers={"origin": "https://other.test"}
        ).status_code
        == 403
    )
