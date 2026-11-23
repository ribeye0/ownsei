"""Destructive release operations run only inside a temporary user profile."""

import importlib
import json
import sqlite3

import pytest


@pytest.fixture
def release(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    uninstall = importlib.import_module("uninstall")
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(tmp_path / name))
    root = tmp_path / "Ownsei"
    (root / "src/onsei").mkdir(parents=True)
    (root / "pyproject.toml").touch()
    (root / "release.json").write_text('{"engines": {}}')
    (root / "src/onsei/cli.py").touch()
    (root / "workspace").mkdir()
    files = dict.fromkeys(["pyproject.toml", "src/onsei/cli.py", "release.json"], "hash")
    (root / "manifest.json").write_text(json.dumps({"files": files}))
    return root, uninstall


def test_preview_and_remove(release, tmp_path, monkeypatch):
    root, uninstall = release
    library = tmp_path / "shared models"
    model = library / "index-nativelong-9b"
    model.mkdir(parents=True)
    (model / "weights.gguf").touch()
    receipt = model / "onsei-model.json"
    receipt.write_text(json.dumps({"repo": "test", "sha256": {"weights.gguf": "0" * 64}}))
    note = model / "my notes.txt"
    note.touch()
    setting = uninstall.library_setting()
    setting.parent.mkdir(parents=True)
    setting.write_text(json.dumps({"models_dir": str(library)}))
    cache = uninstall.download_cache(library) / "downloads/index-nativelong-9b"
    cache.mkdir(parents=True)
    partial = cache / "incomplete"
    partial.touch()
    external = root / ".runtime-envs/user-project"
    external.mkdir(parents=True)
    with sqlite3.connect(root / "workspace/index.db") as db:
        db.execute("CREATE TABLE projects(id,path)")
        db.execute("INSERT INTO projects VALUES ('project',?)", (str(external),))
    source = root / ".runtime-envs/music.wav"
    source.touch()
    subtitle = source.with_suffix(".zh-tw.vtt")
    subtitle.touch()
    with sqlite3.connect(external / "project.db") as db:
        db.execute("CREATE TABLE tracks(payload)")
        db.execute("INSERT INTO tracks VALUES (?)", (json.dumps({"source": str(source)}),))
    kept = [note, source, subtitle, external / "project.db", root / "workspace/index.db"]
    import os

    if os.name != "nt":
        (root / ".runtime-envs/link").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(uninstall, "ROOT", root)
    monkeypatch.setattr(uninstall.sys, "argv", ["uninstall", "--dry-run"])
    uninstall.main()
    assert partial.exists() and receipt.exists()
    monkeypatch.setattr(uninstall.sys, "argv", ["uninstall"])
    monkeypatch.setattr(uninstall, "ensure_idle", lambda: None)
    monkeypatch.setattr("builtins.input", lambda _: "")
    uninstall.main()
    assert setting.exists() and (root / "manifest.json").exists()
    uninstall.remove(uninstall.make_plan(root, []).ordered())
    assert all(p.exists() for p in kept)
    assert not any(
        p.exists() for p in (partial, receipt, setting, model / "weights.gguf", root / "manifest.json")
    )


@pytest.mark.parametrize("name", ["../outside", "C:/outside", "x/../workspace/index.db", "x\\outside"])
def test_unsafe_manifest_refused_before_deletion(release, name):
    root, uninstall = release
    manifest = root / "manifest.json"
    content = json.loads(manifest.read_text())
    content["files"][name] = ""
    manifest.write_text(json.dumps(content))
    with pytest.raises(ValueError):
        uninstall.make_plan(root, [])
    assert (root / "pyproject.toml").exists()


def test_workspace_cannot_be_removed_even_if_selected_as_model_library(release):
    root, uninstall = release
    library = root / "workspace/index-nativelong-9b"
    library.mkdir()
    receipt = library / "onsei-model.json"
    receipt.write_text('{"repo":"test","sha256":{"note":"invalid"}}')
    uninstall.remove(uninstall.make_plan(root, [str(library.parent)]).ordered())
    assert receipt.exists()
