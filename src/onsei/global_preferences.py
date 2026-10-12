"""Atomic global settings; absence seeds defaults, an empty list stays empty."""

import json
from pathlib import Path
from filelock import FileLock

from onsei.terminology import normalize_entries
from onsei.translation_policy import DEFAULT_GLOSSARY
from onsei.util import ROOT, atomic_json

GLOBAL_PATH = ROOT / "workspace/global-preferences.json"


def load_global(path=None):
    path = Path(path or GLOBAL_PATH)
    if not path.exists():
        return {"revision": 0, "glossary_entries": normalize_entries(DEFAULT_GLOSSARY)}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or type(value.get("revision")) is not int or value["revision"] < 0
            or "glossary_entries" not in value):
        raise ValueError("全局術語設定檔格式無效，請檢查 global-preferences.json")
    return {"revision": value["revision"], "glossary_entries": normalize_entries(value["glossary_entries"])}


def save_global(entries, revision, path=None):
    from onsei.storage import ConflictError
    entries = normalize_entries(entries)
    path = Path(path or GLOBAL_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=5):
        current = load_global(path)
        if current["revision"] != revision:
            raise ConflictError("全局術語已被其他視窗修改，請重新載入後再儲存")
        value = {"revision": revision + 1, "glossary_entries": entries}
        atomic_json(path, value)
        return value
