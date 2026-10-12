"""A consistent, read-only snapshot for one track's result controls and editor."""

import json
from collections import defaultdict

from onsei.domain import Cue, Track
from onsei.storage import Store, read_cue
from onsei.snapshots import result_exports
from onsei.translation_contract import polluted
from onsei.translation_runs import run_summary


def track_view(store: Store, track_id: str) -> dict:
    # Preserve the previous result-list endpoint's legacy capture behavior.
    # Migration finishes before the read transaction so the view stays coherent.
    store.ensure_legacy_result(track_id)
    with store.db() as db:
        db.execute("BEGIN")  # All reads, including active result IDs, share one SQLite snapshot.
        row = db.execute("SELECT payload FROM tracks WHERE id=?", (track_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown track")
        track = Track.model_validate_json(row[0])
        grouped = defaultdict(list)
        for (raw,) in db.execute("SELECT payload FROM cues WHERE track_id=? ORDER BY ordinal", (track_id,)):
            cue = Cue.model_validate_json(raw)
            grouped[cue.version].append(cue)
        active = store._active_translation(db, track_id, track.active_version)
        cues = [
            read_cue(c.model_dump_json())
            for c in store.read_version_cues(db, [c.model_dump_json() for c in grouped[track.active_version]])
        ]
        versions = []
        for version, created_at, estimated in db.execute(
            "SELECT version,created_at,estimated FROM transcript_versions WHERE track_id=? ORDER BY rowid DESC",
            (track_id,),
        ):
            if not grouped[version]:
                continue
            source = grouped[version][0].provenance
            versions.append(
                {
                    "version": version,
                    "created_at": created_at,
                    "created_at_estimated": bool(estimated),
                    "cues": len(grouped[version]),
                    "model": source.get("model", "imported"),
                    "processed_duration_ms": source.get("processed_duration_ms"),
                    "limit_ms": source.get("limit_ms"),
                    "extra_alignment": source.get("extra_alignment", False),
                }
            )
        translations = []
        for result_id, model, created_at in db.execute(
            "SELECT id,model,created_at FROM translation_versions WHERE track_id=? AND version=? ORDER BY created_at DESC,rowid DESC",
            (track_id, track.active_version),
        ):
            rows = [
                json.loads(r[0])
                for r in db.execute("SELECT payload FROM translation_cues WHERE result_id=?", (result_id,))
            ]
            translations.append(
                {
                    "id": result_id,
                    "model": model,
                    "created_at": created_at,
                    "active": result_id == active,
                    "translated": sum(v["translation_status"] in ("translated", "manual") for v in rows),
                    "manual": sum(v["zh_override"] is not None for v in rows),
                    "stale": sum(
                        v["translation_status"] == "stale"
                        or (v["zh_override"] is None and polluted(v["zh_auto"]))
                        for v in rows
                    ),
                    **run_summary(store.run_metadata(result_id, db)),
                }
            )
        files = result_exports(db, track_id, cues)
        return {
            "track": track.model_dump(),
            "cues": [{**c.model_dump(), "ja": c.ja, "zh": c.zh} for c in cues],
            "versions": versions,
            "translations": translations,
            "files": files,
        }
