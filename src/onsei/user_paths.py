"""Per-user locations shared by the web server, CLI and inference workers."""

import os
from pathlib import Path
import sys


def config_dir() -> Path:
    windows = sys.platform == "win32"
    base = os.environ.get("APPDATA" if windows else "XDG_CONFIG_HOME")
    if base and Path(base).is_absolute():
        return Path(base) / "ownsei"
    return Path.home() / ("AppData/Roaming" if windows else ".config") / "ownsei"


def models_dir() -> Path:
    return config_dir() / "models"


def locks_dir() -> Path:
    return config_dir() / ".locks"
