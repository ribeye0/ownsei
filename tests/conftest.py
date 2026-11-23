import pytest


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path, monkeypatch):
    # The UI, CLI and any spawned workers must never read a developer's settings or models.
    for variable in ("XDG_CONFIG_HOME", "APPDATA"):
        monkeypatch.setenv(variable, str(tmp_path / "user-config"))
    for variable in ("XDG_DATA_HOME", "LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.setenv(variable, str(tmp_path / variable))
