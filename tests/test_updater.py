import hashlib
import tarfile
import threading
import time
import zipfile

from fastapi.testclient import TestClient
import httpx
import pytest

from onsei import update_install, updater
from onsei.util import atomic_json, file_hash


def installation(path, version, platform):
    path.mkdir(parents=True)
    atomic_json(
        path / "release.json",
        {"version": version, "platform": platform, "architecture": "x64", "backend": "vulkan"},
    )
    (path / "README.md").write_text(version)
    files = {p.name: file_hash(p) for p in path.iterdir()}
    atomic_json(path / "manifest.json", {"format": 1, "files": files})


def staged(tmp_path, platform):
    root = tmp_path / "installed"
    installation(root, "0.2.9", platform)
    cache = update_install.update_dir(root)
    cache.mkdir(parents=True)
    target = platform + "-x64-vulkan"
    prefix = "ownsei-0.2.10-" + target
    release = tmp_path / prefix
    installation(release, "0.2.10", platform)
    archive = cache / "package"
    if platform == "windows":
        with zipfile.ZipFile(archive, "w") as z:
            for p in release.iterdir():
                z.write(p, prefix + "/" + p.name)
    else:
        with tarfile.open(archive, "w:gz") as t:
            t.add(release, arcname=prefix)
    offer = {
        "id": 7,
        "target": target,
        "version": "0.2.10",
        "total": archive.stat().st_size,
        "sha256": file_hash(archive),
    }
    atomic_json(cache / "ready.json", offer)
    return root, offer


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_apply_and_recover_preserve_user_data(tmp_path, platform):
    root, _ = staged(tmp_path, platform)
    sentinel = root / "workspace" / "project.db"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"user content")
    update_install.apply(root)
    assert (root / "README.md").read_text() == "0.2.10"
    assert update_install.rollback(root)
    assert (root / "README.md").read_text() == "0.2.9"
    assert not update_install.rollback(root)
    assert sentinel.read_bytes() == b"user content"


@pytest.mark.parametrize(
    "name",
    [
        "../x",
        "workspace/project.db",
        "scripts/../../x",
        "scripts/X:y",
        ".venv/file",
        "docs/CON",
        "bin/.. /file",
        "/usr/bin",
    ],
)
def test_reject_non_release_paths(name):
    with pytest.raises(ValueError):
        update_install.managed(name)


def test_corrupt_archive_fails_before_modifying_files(tmp_path):
    root, _ = staged(tmp_path, "linux")
    (update_install.update_dir(root) / "package").write_bytes(b"bad")
    with pytest.raises(ValueError, match="SHA-256"):
        update_install.apply(root)
    assert not update_install.rollback(root)
    assert (root / "README.md").read_text() == "0.2.9"


def test_check_download_progress_reopen_and_manual_install(tmp_path, monkeypatch):
    import onsei.api as api

    root = tmp_path / "install"
    installation(root, "0.2.9", "linux")
    monkeypatch.setenv("OWNSEI_UPDATE_LAUNCHER", "1")
    manager = updater.Updater(root)
    monkeypatch.setattr(api, "updates", manager)
    monkeypatch.setattr(api, "REGISTRY", root / "workspace/index.db")
    monkeypatch.setattr("onsei.modelmanager.STATE", root / ".runtime-envs")
    ready, resume = threading.Event(), threading.Event()
    block = b"x" * 131072
    digest = hashlib.sha256(block * 2).hexdigest()

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            yield block
            ready.set()
            assert resume.wait(5)
            yield block

    def github(request):
        if request.url.path.endswith("/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v0.2.10",
                    "assets": [
                        {
                            "state": "uploaded",
                            "name": "ownsei-0.2.10-linux-x64-vulkan.tar.gz",
                            "id": 11,
                            "size": len(block) * 2,
                            "digest": "sha256:" + digest,
                        }
                    ],
                },
            )
        assert request.url.path.endswith("/assets/11")
        return httpx.Response(200, stream=SlowStream())

    client_factory = httpx.Client

    def wait_phase(phase):
        for _ in range(250):
            if manager.status()["phase"] == phase:
                return
            time.sleep(0.01)
        pytest.fail(str(manager.status()))

    with TestClient(api.app, base_url="http://localhost") as web:
        monkeypatch.setattr(
            updater.httpx, "Client", lambda **kw: client_factory(transport=httpx.MockTransport(github), **kw)
        )
        web.post("/api/update/check")
        wait_phase("available")
        web.post("/api/update/download")
        assert ready.wait(5)
        assert web.get("/api/update").json()["bytes"] == len(block)
        assert web.post("/api/update/install").status_code == 409
        project = web.post("/api/projects", json={"title": "背景下載中建立"}).json()["id"]
        assert web.get("/api/projects").json()[0]["id"] == project
        resume.set()
        wait_phase("ready")
        assert updater.Updater(root).status()["phase"] == "ready"
        again = web.post("/api/update/check").json()
        assert again["phase"] in ("checking", "ready")
        wait_phase("ready")
        monkeypatch.setattr(api, "latest_import", lambda _: {"status": "running"})
        assert web.post("/api/update/install").status_code == 409
        assert manager.status()["phase"] == "ready"
        monkeypatch.setattr(api, "latest_import", lambda _: None)
        try:
            assert web.post("/api/update/install").json()["phase"] == "installing"
            assert web.post("/api/projects", json={"title": "after"}).status_code == 409
        finally:
            api.app.state.update_drain = False


def test_release_checksum_fallback_and_private_redirect(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "fixture")
    sha = "0" * 64
    name = "ownsei-2.0.0-windows-x64-vulkan.zip"

    def github(request):
        if request.url.host != "api.github.com":
            assert "authorization" not in request.headers
            return httpx.Response(200, text=sha + "  " + name + "\n")
        assert request.headers["authorization"] == "Bearer fixture"
        if request.url.path.endswith("/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v2.0.0",
                    "assets": [
                        {"name": name, "id": 1, "size": 20, "state": "uploaded"},
                        {"name": "SHA256SUMS", "id": 2, "state": "uploaded"},
                    ],
                },
            )
        return httpx.Response(302, headers={"Location": "https://download.example/checksum"})

    with httpx.Client(transport=httpx.MockTransport(github), follow_redirects=True) as client:
        assert updater.release_offer(client, "1.9.0", "windows-x64-vulkan")["sha256"] == sha
        assert updater.release_offer(client, "2.1.0", "windows-x64-vulkan") == {"version": "2.0.0"}


def test_interrupted_file_replacement_can_be_rolled_back(tmp_path, monkeypatch):
    root, _ = staged(tmp_path, "windows")
    copy = update_install.copy_atomic

    def fail_once(source, dest):
        if dest.name == "manifest.json":
            raise OSError("interrupted")
        copy(source, dest)

    with monkeypatch.context() as m:
        m.setattr(update_install, "copy_atomic", fail_once)
        with pytest.raises(OSError, match="interrupted"):
            update_install.apply(root)
    assert (root / "README.md").read_text() == "0.2.10"
    assert update_install.rollback(root)
    assert (root / "README.md").read_text() == "0.2.9"


def test_verified_download_remains_installable_offline(tmp_path, monkeypatch):
    root, _ = staged(tmp_path, "windows")
    monkeypatch.setenv("OWNSEI_UPDATE_LAUNCHER", "1")
    manager = updater.Updater(root)

    def offline(*args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(updater, "release_offer", offline)
    manager.worker("check")
    assert manager.status()["phase"] == "ready"
    assert manager.status()["error"]


def test_browser_loads_new_frontend_after_restart_with_matching_file_metadata(tmp_path, monkeypatch):
    import os
    import onsei.api as api

    monkeypatch.setattr(api, "static", tmp_path)
    index = tmp_path / "index.html"
    index.write_text("old")
    os.utime(index, (0, 0))
    with TestClient(api.app, base_url="http://localhost") as web:
        old = web.get("/")
        index.write_text("new")
        os.utime(index, (0, 0))
        response = web.get("/", headers={"If-None-Match": old.headers["etag"]})
        assert response.text == "new"
        assert response.headers["cache-control"] == "no-store"


def test_native_launcher_restarts_on_same_port_after_manual_install(tmp_path):
    """Run the real launcher, API, platform installer invocation and restart handshake."""
    import json
    import os
    from pathlib import Path
    import shutil
    import socket
    import subprocess
    import sys

    import psutil

    platform = "windows" if os.name == "nt" else "linux"
    root, offer = staged(tmp_path, platform)
    scripts = root / "scripts"
    scripts.mkdir()
    launcher = scripts / "launch.py"
    shutil.copy2(Path(__file__).resolve().parents[1] / "scripts/launch.py", launcher)
    # Dependencies are supplied by the test environment. Keep this test offline.
    (scripts / "install.ps1").write_text("Set-Content -Path setup-ran -Value ok\n")
    (scripts / "install.sh").write_text("echo ok > setup-ran\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = tmp_path / "launcher.log"
    with log.open("w") as output, httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
        process = subprocess.Popen(
            [sys.executable, launcher, "--port", str(port), "--no-browser"],
            cwd=root,
            stdout=output,
            stderr=output,
        )

        def wait_for_version(expected):
            for _ in range(300):
                assert process.poll() is None, log.read_text(encoding="utf-8", errors="replace")
                try:
                    state = client.get("/api/update").json()
                    if state["current"] == expected:
                        return state
                    if state["error"]:
                        pytest.fail(state["error"])
                except (httpx.TransportError, json.JSONDecodeError):
                    pass
                time.sleep(0.1)
            pytest.fail(log.read_text(encoding="utf-8", errors="replace"))

        try:
            assert wait_for_version("0.2.9")["phase"] == "ready"
            project = client.post("/api/projects", json={"title": "update test"}).json()["id"]
            assert client.post("/api/update/install", json={}).json()["phase"] == "installing"
            assert wait_for_version(offer["version"])["phase"] == "idle"
            assert (root / "setup-ran").is_file()
            assert client.get("/api/projects").json()[0]["id"] == project
        finally:
            if process.poll() is None:
                children = psutil.Process(process.pid).children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(children, timeout=5)
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
