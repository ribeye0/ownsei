"""Per-user locations shared by the web server, CLI and inference workers."""

import json
import os
from pathlib import Path
import sys


def _base(windows_var, windows_default, linux_var, linux_default):
    windows = sys.platform == "win32"
    base = os.environ.get(windows_var if windows else linux_var)
    if base and Path(base).is_absolute():
        return Path(base) / "ownsei"
    return Path.home() / (windows_default if windows else linux_default) / "ownsei"


def config_dir() -> Path:
    return _base("APPDATA", "AppData/Roaming", "XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _base("LOCALAPPDATA", "AppData/Local", "XDG_DATA_HOME", ".local/share")


def library_setting() -> Path:
    return (data_dir() if sys.platform == "win32" else config_dir()) / "storage.json"


def models_dir() -> Path:
    try:
        saved = json.loads(library_setting().read_text(encoding="utf-8"))
        path = Path(saved["models_dir"])
        if not path.is_absolute():
            raise ValueError
        return path
    except FileNotFoundError:
        legacy = config_dir() / "models"
        if legacy.is_dir() and any(p.is_dir() and not p.name.startswith(".") for p in legacy.iterdir()):
            return legacy
        return data_dir() / "models"
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError(f"模型庫設定無效：{library_setting()}") from error


def download_cache(library=None) -> Path:
    root = library if library is not None else models_dir()
    if root in {data_dir() / "models", config_dir() / "models"}:
        if sys.platform == "win32":
            return data_dir() / "cache"
        return _base("LOCALAPPDATA", "AppData/Local", "XDG_CACHE_HOME", ".cache")
    # A mounted volume itself cannot use a sibling on its parent's disk.
    if root.is_mount() or root == root.parent:
        return root / ".ownsei-download-cache"
    return root.with_name(root.name + ".ownsei-download-cache")


def locks_dir() -> Path:
    return config_dir() / ".locks"
