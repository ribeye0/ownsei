from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest

from onsei import models
from onsei.api import app
from onsei.global_preferences import global_path, load_global
from onsei.migrate_user_data import import_user_data
from onsei.user_paths import config_dir, data_dir, models_dir


def test_config_follows_platform_not_install_location(tmp_path, monkeypatch):
    roaming, xdg = tmp_path / "Roaming", tmp_path / "XDG"
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(sys, "platform", "win32")
    assert config_dir() == roaming / "ownsei"
    monkeypatch.delenv("APPDATA")
    assert config_dir() == Path.home() / "AppData/Roaming/ownsei"
    monkeypatch.setattr(sys, "platform", "linux")
    assert config_dir() == xdg / "ownsei"
    for invalid in ("relative", ""):
        monkeypatch.setenv("XDG_CONFIG_HOME", invalid)
        assert config_dir() == Path.home() / ".config/ownsei"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert config_dir() == Path.home() / ".config/ownsei"


def test_import_copies_complete_directories_and_preserves_newer_settings(tmp_path):
    old = tmp_path / "Old Ownsei"
    model = "haas-ja-v1.0-f16"
    source = old / "models" / model
    source.mkdir(parents=True)
    (source / "onsei-model.json").write_text("{}")
    (source / "weights.gguf").write_bytes(b"gguf")
    (source / ".cache").mkdir()
    (source / ".cache/partial").touch()
    (old / "workspace").mkdir()
    (old / "workspace/global-preferences.json").write_text('{"revision":3,"glossary_entries":[]}')
    assert set(import_user_data(old)["imported"]) == {global_path().name, model}
    assert models.model_dir(model) == data_dir() / "models" / model
    assert not (models.model_dir(model) / ".cache").exists() and (source / ".cache/partial").exists()
    assert load_global() == {"revision": 3, "glossary_entries": []}
    target = models.model_dir(model) / "weights.gguf"
    assert target.read_bytes() == b"gguf"
    target.write_bytes(b"newer")
    assert import_user_data(old)["imported"] == []
    assert target.read_bytes() == b"newer" and (source / "weights.gguf").read_bytes() == b"gguf"


def test_interrupted_model_import_leaves_no_partial_model(tmp_path, monkeypatch):
    import shutil

    model = "index-nativelong-9b"
    (tmp_path / "models" / model).mkdir(parents=True)

    def interrupted(source, dest, **kwargs):
        dest.mkdir()
        (dest / "partial").touch()
        raise OSError("full disk")

    monkeypatch.setattr(shutil, "copytree", interrupted)
    with pytest.raises(OSError, match="full disk"):
        import_user_data(tmp_path)
    assert list(models_dir().iterdir()) == []


def test_frontend_import_only_seeds_missing_preferences_and_patches_independent_keys():
    client = TestClient(app, base_url="http://127.0.0.1")
    url = "/api/preferences/interface"
    assert client.post(url, json={"values": {"theme": "mocha"}}).is_success
    assert client.post(
        url, json={"values": {"theme": "classic", "locale": "auto"}, "import_missing": True}
    ).json() == {"theme": "mocha", "locale": "auto"}
    assert client.post(url, json={"values": {"locale": "zh-Hans"}}).json() == {
        "theme": "mocha",
        "locale": "zh-Hans",
    }
    assert (config_dir() / "interface.json").is_file()
    assert client.post(url, json={"values": {"project": "private"}}).status_code == 422
