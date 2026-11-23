"""Import settings and model directories from an older installation."""

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from filelock import FileLock

from onsei.models import REGISTRY, model_dir
from onsei.global_preferences import load_global
from onsei.user_paths import config_dir, locks_dir
from onsei.util import atomic_json


def import_user_data(installation: Path):
    source = installation / "models"
    glossary = installation / "workspace/global-preferences.json"
    if not source.is_dir() and not glossary.is_file():
        raise ValueError(f"找不到舊版模型或全局設定：{installation}")
    copied, existing = [], []
    if glossary.is_file():
        destination = config_dir() / glossary.name
        with FileLock(str(destination) + ".lock", timeout=5):
            if destination.exists():
                existing.append(glossary.name)
            else:
                load_global(glossary)  # Validate the revision and terminology before publishing it.
                atomic_json(destination, json.loads(glossary.read_text(encoding="utf-8")))
                copied.append(glossary.name)
    with FileLock(locks_dir() / "model-operation.lock"):
        for name in REGISTRY:
            new = model_dir(name)
            old = source / new.name
            if not old.is_dir():
                continue
            if new.exists() or new.is_symlink():
                existing.append(name)
                continue
            new.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=new.parent, prefix=".import-") as staging:
                staged = Path(staging) / name
                shutil.copytree(old, staged, ignore=shutil.ignore_patterns(".cache"))
                staged.rename(new)
            copied.append(name)
    return {"directory": str(config_dir()), "imported": copied, "already_present": existing}
