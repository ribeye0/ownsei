from __future__ import annotations

import json
import time

from onsei.domain import Cue
from onsei.storage import Store
from onsei.util import digest
from onsei.translation_contract import polluted


def cue_digest(cues: list[Cue]) -> str:
    return digest(
        [
            {
                "id": c.id,
                "ja": c.ja,
                "zh": c.zh,
                "start_ms": c.start_ms,
                "end_ms": c.end_ms,
                "timing": c.timing_status,
                "translation": c.translation_status,
            }
            for c in cues
        ]
    )


def register_export(store: Store, track_id: str, cues: list[Cue], names: list[str], db=None):
    key = export_key(track_id, cues)
    value = json.dumps({"digest": cue_digest(cues), "files": names, "time": time.time()})

    def write(connection):
        connection.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
            (key, value),
        )

    if db is not None:
        write(db)
    else:
        with store.db(write=True) as connection:
            write(connection)


def fresh_exports(store: Store) -> list[str]:
    names = []
    with store.db() as db:
        db.execute("BEGIN")
        for (track_id,) in db.execute("SELECT id FROM tracks").fetchall():
            _, cues = store.read_result(db, track_id)
            names.extend(result_exports(db, track_id, cues))
    return names


def result_exports(db, track_id: str, cues: list[Cue]) -> list[str]:
    """Use the same snapshot and validity rules for download and result controls."""
    row = db.execute("SELECT payload FROM settings WHERE key=?", (export_key(track_id, cues),)).fetchone()
    if row is None:
        row = db.execute("SELECT payload FROM settings WHERE key=?", (f"exports:{track_id}",)).fetchone()
    if row is None:
        return []
    value = json.loads(row[0])
    if value["digest"] != cue_digest(cues):
        return []
    files = value["files"]
    identity = cues[0].provenance.get("translation_version") if cues else None
    metadata_row = db.execute(
        "SELECT payload FROM translation_runs WHERE result_id=?", (identity,)
    ).fetchone()
    metadata = json.loads(metadata_row[0]) if metadata_row else {}
    if (
        metadata.get("status") == "stale"
        or (metadata.get("stage") == "review" and metadata.get("status") != "completed")
        or any(c.zh_override is None and polluted(c.zh_auto) for c in cues)
    ):
        files = [
            name
            for name in files
            if name.endswith((".jsonl", ".ja.txt", ".ja.srt", ".ja.vtt", ".ja.ass", ".ja.lrc"))
        ]
    return files


def export_key(track_id: str, cues: list[Cue]) -> str:
    if not cues:
        return f"exports:{track_id}:none"
    return f"exports:{track_id}:{cues[0].version}:{cues[0].provenance.get('translation_version', 'legacy')}"
