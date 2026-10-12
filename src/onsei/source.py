"""Verify original audio identity before using decoders or their caches."""

import json
from pathlib import Path

from filelock import FileLock

from onsei.domain import Track
from onsei.storage import Store, ConflictError
from onsei.util import file_hash


def signature(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def validate_source(store: Store, track: Track) -> list[int]:
    source = Path(track.source)
    if not source.is_file():
        raise ValueError("原音檔不存在，請檢查是否已移動或刪除。")
    before = signature(source)
    if track.source_signature == before:
        return before
    with FileLock(store.path / f".source-{track.id}.lock", timeout=30):
        current = store.track(track.id)
        before = signature(source)
        if current.source_signature == before:
            track.source_signature = before
            return before
        # Legacy imports already have a verified hash/signature in this cache.
        try:
            saved = json.loads((store.path / "import-checksums.json").read_text()).get(str(source), {})
        except (FileNotFoundError, ValueError, AttributeError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        matched = saved.get("sha256") == track.sha256 and saved.get("signature") == before
        if not matched and file_hash(source) != track.sha256:
            raise ConflictError("原音檔內容已變更，已停止使用舊快取；請重新匯入音檔，舊逐字稿仍保留。")
        if signature(source) != before:
            raise ConflictError("驗證期間原音檔已變更，請完成檔案寫入後重新匯入。")
        with store.db(write=True) as db:
            current = Track.model_validate_json(
                db.execute("SELECT payload FROM tracks WHERE id=?", (track.id,)).fetchone()[0]
            )
            current.source_signature = before
            db.execute("UPDATE tracks SET payload=? WHERE id=?", (current.model_dump_json(), track.id))
        track.source_signature = before
        return before
