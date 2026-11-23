"""Persist independent translation results for each Japanese transcript.

Japanese text/timing/revisions remain in cues. Chinese edits and inference belong
only to a selected translation version; legacy data is migrated losslessly.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from onsei.domain import Cue
from onsei.translation_contract import polluted
from onsei.translation_runs import TranslationRuns, run_summary

ZH_FIELDS = ("zh_auto", "zh_override", "translation_status")


def timing_metadata(source):
    legacy = source.get("prototype_run", {})
    return {
        "asr_filter": source.get("vad", {}).get("settings"),
        "segmentation": source.get("segmentation")
        or (
            {"engine": legacy["vad"], "settings": legacy.get("vad_config", {})} if legacy.get("vad") else None
        ),
    }


class VersionStore(TranslationRuns):
    def migrate_versions(self):
        with self.db() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] < 2:
                if db.execute("SELECT COUNT(*) FROM cues").fetchone()[0]:
                    backup = self.path / "project-before-versions.db"
                    if not backup.exists():
                        temporary = backup.with_suffix(".tmp.db")
                        with sqlite3.connect(temporary) as target:
                            db.backup(target)
                        temporary.replace(backup)
                db.execute("BEGIN IMMEDIATE")
                for statement in """
                    CREATE TABLE IF NOT EXISTS translation_versions(
                        id TEXT PRIMARY KEY, track_id TEXT, version TEXT, model TEXT, created_at REAL);
                    CREATE TABLE IF NOT EXISTS translation_cues(
                        result_id TEXT, cue_id TEXT, payload TEXT, PRIMARY KEY(result_id,cue_id));
                    CREATE TABLE IF NOT EXISTS active_translations(
                        track_id TEXT, version TEXT, result_id TEXT, PRIMARY KEY(track_id,version));
                """.split(";"):
                    if statement.strip():
                        db.execute(statement)
                for row in db.execute("SELECT DISTINCT track_id,version FROM cues").fetchall():
                    self._capture_legacy(db, row[0], row[1])
                db.execute("PRAGMA user_version=2")
            if db.execute("PRAGMA user_version").fetchone()[0] < 3:
                backup = self.path / "project-before-result-times.db"
                # End the legacy migration transaction before SQLite backup.
                db.commit()
                if not backup.exists():
                    with sqlite3.connect(backup) as target:
                        db.backup(target)
                db.execute("""CREATE TABLE IF NOT EXISTS transcript_versions(
                    track_id TEXT, version TEXT, created_at REAL, estimated INTEGER,
                    PRIMARY KEY(track_id,version))""")
                rows = db.execute("""SELECT track_id,version,MIN(rowid) AS insertion_order
                    FROM cues GROUP BY track_id,version ORDER BY insertion_order""").fetchall()
                for track_id, version, _ in rows:
                    raw = db.execute(
                        "SELECT payload FROM cues WHERE track_id=? AND version=?", (track_id, version)
                    ).fetchall()
                    files = []
                    for (payload,) in raw:
                        provenance = json.loads(payload).get("provenance", {})
                        cache = (
                            provenance.get("alignment_diagnostic", {}).get("cache_file")
                            if provenance.get("extra_alignment")
                            else provenance.get("chunk", {}).get("output")
                        )
                        if cache:
                            path = Path(cache)
                            if path.is_file() and path.resolve().is_relative_to(
                                (self.path / "cache").resolve()
                            ):
                                files.append(path.stat().st_mtime)
                    db.execute(
                        "INSERT OR IGNORE INTO transcript_versions VALUES (?,?,?,?)",
                        (track_id, version, max(files) if files else None, 1),
                    )
                db.execute("PRAGMA user_version=3")
            if db.execute("PRAGMA user_version").fetchone()[0] < 4:
                db.commit()
                backup = self.path / "project-before-translation-runs.db"
                if not backup.exists():
                    temporary = backup.with_suffix(".tmp.db")
                    with sqlite3.connect(temporary) as target:
                        db.backup(target)
                    temporary.replace(backup)
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS translation_runs(result_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                db.execute("PRAGMA user_version=4")

    def transcript_versions(self, track_id):
        with self.db() as db:
            rows = db.execute(
                """SELECT version,created_at,estimated FROM transcript_versions
                WHERE track_id=? ORDER BY rowid DESC""",
                (track_id,),
            ).fetchall()
        results = []
        for version, created_at, estimated in rows:
            cues = self.cues(track_id, version)
            if not cues:
                continue
            source = cues[0].provenance
            results.append(
                {
                    "version": version,
                    "cues": len(cues),
                    "created_at": created_at,
                    "created_at_estimated": bool(estimated),
                    "model": source.get("model", "imported"),
                    "processed_duration_ms": source.get("processed_duration_ms"),
                    "limit_ms": source.get("limit_ms"),
                    "extra_alignment": source.get("extra_alignment", False),
                    "use_vad": source.get("vad", {}).get("active", False),
                    **timing_metadata(source),
                    "vad_fallback": bool(source.get("vad", {}).get("fallback")),
                }
            )
        return results

    @staticmethod
    def _active_translation(db, track_id, version):
        row = db.execute(
            "SELECT result_id FROM active_translations WHERE track_id=? AND version=?", (track_id, version)
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _zh_payload(cue):
        return {
            **{key: getattr(cue, key) for key in ZH_FIELDS},
            "translation": cue.provenance.get("translation"),
            "translation_flags": [f for f in cue.review_flags if f.startswith("translation_")],
            "review": cue.provenance.get("review"),
            "training": cue.provenance.get("training"),
        }

    def _capture_legacy(self, db, track_id, version, default_model="legacy"):
        rows = db.execute(
            "SELECT payload FROM cues WHERE track_id=? AND version=? ORDER BY ordinal", (track_id, version)
        ).fetchall()
        cues = [Cue.model_validate_json(row[0]) for row in rows]
        slots = {}
        last = None
        known_models = list(
            dict.fromkeys(
                c.provenance.get("translation", {}).get("model")
                for c in cues
                if c.provenance.get("translation", {}).get("model")
            )
        )
        for cue in cues:
            if cue.zh_auto is None and cue.zh_override is None and not cue.provenance.get("translation"):
                continue
            explicit_model = cue.provenance.get("translation", {}).get("model")
            # Old manual overrides were global to the transcript. When no model
            # was recorded, retain them in each existing model's migrated result.
            models = (
                [explicit_model]
                if explicit_model
                else (known_models if cue.zh_override is not None and known_models else [default_model])
            )
            for model in models:
                if model not in slots:
                    found = db.execute(
                        "SELECT id FROM translation_versions WHERE track_id=? AND version=? AND model=?",
                        (track_id, version, model),
                    ).fetchone()
                    slots[model] = found[0] if found else uuid.uuid4().hex[:16]
                    if not found:
                        db.execute(
                            "INSERT INTO translation_versions VALUES (?,?,?,?,?)",
                            (slots[model], track_id, version, model, time.time()),
                        )
                last = slots[model]
                db.execute(
                    "INSERT OR IGNORE INTO translation_cues VALUES (?,?,?)",
                    (last, cue.id, json.dumps(self._zh_payload(cue), ensure_ascii=False)),
                )
            cue.zh_auto = cue.zh_override = None
            cue.translation_status = "pending"
            cue.provenance.pop("translation", None)
            cue.provenance.pop("translation_version", None)
            db.execute("UPDATE cues SET payload=? WHERE id=?", (cue.model_dump_json(), cue.id))
        if last and not self._active_translation(db, track_id, version):
            db.execute("INSERT OR REPLACE INTO active_translations VALUES (?,?,?)", (track_id, version, last))

    def ensure_legacy_result(self, track_id):
        with self.db() as db:
            row = db.execute("SELECT payload FROM tracks WHERE id=?", (track_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown track")
            version = json.loads(row[0]).get("active_version")
            needs_capture = db.execute(
                """SELECT 1 FROM cues WHERE track_id=? AND version=? AND
                (json_extract(payload,'$.zh_auto') IS NOT NULL OR
                 json_extract(payload,'$.zh_override') IS NOT NULL OR
                 json_extract(payload,'$.provenance.translation') IS NOT NULL) LIMIT 1""",
                (track_id, version),
            ).fetchone()
        if needs_capture:
            with self.db(write=True) as db:
                self._capture_legacy(db, track_id, version)

    def _read_version_cue(self, db, raw):
        cue = Cue.model_validate_json(raw)
        active = self._active_translation(db, cue.track_id, cue.version)
        if active:
            row = db.execute(
                "SELECT payload FROM translation_cues WHERE result_id=? AND cue_id=?", (active, cue.id)
            ).fetchone()
            value = (
                json.loads(row[0])
                if row
                else {"zh_auto": None, "zh_override": None, "translation_status": "pending"}
            )
            for key in ZH_FIELDS:
                setattr(cue, key, value[key])
            cue.provenance.pop("translation", None)
            if value.get("translation"):
                cue.provenance["translation"] = value["translation"]
            cue.provenance["translation_version"] = active
            cue.provenance.pop("training", None)
            if value.get("training"):
                cue.provenance["training"] = value["training"]
            cue.review_flags += value.get("translation_flags", [])
            if value.get("review"):
                cue.provenance["review"] = value["review"]
        return cue

    def read_version_cues(self, db, raws):
        """Attach the selected Chinese result once per transcript, within the caller's transaction."""
        cues = [Cue.model_validate_json(raw) for raw in raws]
        if not cues:
            return []
        active = self._active_translation(db, cues[0].track_id, cues[0].version)
        if not active:
            return cues
        values = {
            r[0]: json.loads(r[1])
            for r in db.execute("SELECT cue_id,payload FROM translation_cues WHERE result_id=?", (active,))
        }
        for cue in cues:
            value = values.get(
                cue.id, {"zh_auto": None, "zh_override": None, "translation_status": "pending"}
            )
            for key in ZH_FIELDS:
                setattr(cue, key, value[key])
            cue.provenance.pop("translation", None)
            if value.get("translation"):
                cue.provenance["translation"] = value["translation"]
            cue.provenance["translation_version"] = active
            cue.provenance.pop("training", None)
            if value.get("training"):
                cue.provenance["training"] = value["training"]
            cue.review_flags += value.get("translation_flags", [])
            if value.get("review"):
                cue.provenance["review"] = value["review"]
        return cues

    def _write_version_cue(self, db, cue):
        base = cue.model_copy(deep=True)
        active = self._active_translation(db, cue.track_id, cue.version)
        if active:
            db.execute(
                "INSERT OR REPLACE INTO translation_cues VALUES (?,?,?)",
                (active, cue.id, json.dumps(self._zh_payload(cue), ensure_ascii=False)),
            )
            base.zh_auto = base.zh_override = None
            base.translation_status = "pending"
            base.provenance.pop("translation", None)
            base.provenance.pop("translation_version", None)
            base.provenance.pop("training", None)
            base.provenance.pop("review", None)
            base.review_flags = [f for f in base.review_flags if not f.startswith("translation_")]
        db.execute("UPDATE cues SET payload=? WHERE id=?", (base.model_dump_json(), cue.id))

    def _invalidate_versions(self, db, cue_ids):
        for cue_id in cue_ids:
            for row in db.execute(
                "SELECT result_id,payload FROM translation_cues WHERE cue_id=?", (cue_id,)
            ).fetchall():
                value = json.loads(row[1])
                if value["zh_override"] is None:
                    value["translation_status"] = "stale"
                    db.execute(
                        "UPDATE translation_cues SET payload=? WHERE result_id=? AND cue_id=?",
                        (json.dumps(value, ensure_ascii=False), row[0], cue_id),
                    )

    def _invalidate_document_translations(self, db, track_id, version):
        # A source edit anywhere can change the interpretation of a distant
        # line in a document-context result, including inactive result versions.
        rows = db.execute(
            """SELECT c.result_id,c.cue_id,c.payload FROM translation_cues c
            JOIN translation_versions v ON v.id=c.result_id
            WHERE v.track_id=? AND v.version=?""",
            (track_id, version),
        ).fetchall()
        for result_id, cue_id, raw in rows:
            value = json.loads(raw)
            if value.get("zh_override") is None and (value.get("translation") or {}).get("document_key"):
                value["translation_status"] = "stale"
                db.execute(
                    "UPDATE translation_cues SET payload=? WHERE result_id=? AND cue_id=?",
                    (json.dumps(value, ensure_ascii=False), result_id, cue_id),
                )

    def _invalidate_history_translations(self, db, result_id, ordinal):
        """A corrected Chinese reference invalidates its machine-generated descendants."""
        affected, changed = {str(ordinal + 1)}, []
        rows = db.execute(
            """SELECT c.cue_id,c.payload,b.payload FROM translation_cues c
            JOIN cues b ON b.id=c.cue_id WHERE c.result_id=?
            ORDER BY json_extract(b.payload,'$.ordinal')""",
            (result_id,),
        ).fetchall()
        for cue_id, raw, source in rows:
            value = json.loads(raw)
            references = (value.get("translation") or {}).get("document_history", [])
            if value.get("zh_override") is None and any(r.get("id") in affected for r in references):
                value["translation_status"] = "stale"
                affected.add(str(json.loads(source)["ordinal"] + 1))
                changed.append(cue_id)
                db.execute(
                    "UPDATE translation_cues SET payload=? WHERE result_id=? AND cue_id=?",
                    (json.dumps(value, ensure_ascii=False), result_id, cue_id),
                )
        if changed:
            metadata = self.run_metadata(result_id, db)
            metadata.update(
                status="partial", coverage=[i for i in metadata.get("coverage", []) if i not in changed]
            )
            db.execute(
                "UPDATE translation_runs SET payload=? WHERE result_id=?",
                (json.dumps(metadata, ensure_ascii=False), result_id),
            )

    def translation_versions(self, track_id, version=None):
        version = version or self.track(track_id).active_version
        if not version:
            return []
        with self.db(write=True) as db:
            self._capture_legacy(db, track_id, version)
            active = self._active_translation(db, track_id, version)
            results = []
            for row in db.execute(
                "SELECT * FROM translation_versions WHERE track_id=? AND version=? ORDER BY created_at DESC,rowid DESC",
                (track_id, version),
            ).fetchall():
                values = [
                    json.loads(r[0])
                    for r in db.execute("SELECT payload FROM translation_cues WHERE result_id=?", (row[0],))
                ]
                for value in values:
                    if value["zh_override"] is None and polluted(value["zh_auto"]):
                        value["translation_status"] = "stale"
                results.append(
                    {
                        "id": row[0],
                        "model": row[3],
                        "created_at": row[4],
                        "active": row[0] == active,
                        "translated": sum(
                            v["translation_status"] in ("translated", "manual") for v in values
                        ),
                        "manual": sum(v["zh_override"] is not None for v in values),
                        "stale": sum(v["translation_status"] == "stale" for v in values),
                        **run_summary(self.run_metadata(row[0], db)),
                    }
                )
            return results

    def prepare_translation(self, track_id, model):
        version = self.track(track_id).active_version
        if not version:
            raise ValueError("No Japanese transcript")
        with self.db(write=True) as db:
            self._capture_legacy(db, track_id, version, model)
            row = db.execute(
                "SELECT id FROM translation_versions WHERE track_id=? AND version=? AND model=?",
                (track_id, version, model),
            ).fetchone()
            identity = row[0] if row else uuid.uuid4().hex[:16]
            if not row:
                db.execute(
                    "INSERT INTO translation_versions VALUES (?,?,?,?,?)",
                    (identity, track_id, version, model, time.time()),
                )
            self._activate_translation(db, track_id, version, identity)
        return identity

    def activate_translation(self, track_id, identity):
        version = self.track(track_id).active_version
        with self.db(write=True) as db:
            row = db.execute(
                "SELECT id FROM translation_versions WHERE id=? AND track_id=? AND version=?",
                (identity, track_id, version),
            ).fetchone()
            if not row:
                raise ValueError("Unknown translation version for this transcript")
            self._activate_translation(db, track_id, version, identity)

    def _activate_translation(self, db, track_id, version, identity):
        if self._active_translation(db, track_id, version) != identity:
            for row in db.execute(
                "SELECT payload FROM cues WHERE track_id=? AND version=?", (track_id, version)
            ).fetchall():
                cue = Cue.model_validate_json(row[0])
                cue.revision += 1
                db.execute("UPDATE cues SET payload=? WHERE id=?", (cue.model_dump_json(), cue.id))
        db.execute("INSERT OR REPLACE INTO active_translations VALUES (?,?,?)", (track_id, version, identity))

    def translation_nonce(self, track_id, model):
        key = f"translation-generation:{track_id}:{self.track(track_id).active_version}:{model}"
        with self.db() as db:
            row = db.execute("SELECT payload FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else 0

    def delete_translation(self, track_id, identity):
        version = self.track(track_id).active_version
        with self.db(write=True) as db:
            row = db.execute(
                "SELECT model FROM translation_versions WHERE id=? AND track_id=? AND version=?",
                (identity, track_id, version),
            ).fetchone()
            if not row:
                raise ValueError("Unknown translation version for this transcript")
            # Archive before removal, including manual Chinese edits.
            self._archive_result(db, track_id, version, identity)
            db.execute("DELETE FROM translation_cues WHERE result_id=?", (identity,))
            db.execute("DELETE FROM translation_versions WHERE id=?", (identity,))
            db.execute("DELETE FROM translation_runs WHERE result_id=?", (identity,))
            if self._active_translation(db, track_id, version) == identity:
                other = db.execute(
                    "SELECT id FROM translation_versions WHERE track_id=? AND version=? ORDER BY created_at DESC LIMIT 1",
                    (track_id, version),
                ).fetchone()
                self._activate_translation(db, track_id, version, other[0] if other else None)
            key = f"translation-generation:{track_id}:{version}:{row[0]}"
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(uuid.uuid4().hex)))

    def _archive_result(self, db, track_id, version, identity):
        directory = self.path / "trash" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        metadata = db.execute("SELECT * FROM translation_versions WHERE id=?", (identity,)).fetchone()
        rows = db.execute(
            "SELECT cue_id,payload FROM translation_cues WHERE result_id=?", (identity,)
        ).fetchall()
        (directory / "translation.json").write_text(
            json.dumps(
                {
                    "metadata": list(metadata),
                    "run": self.run_metadata(identity, db),
                    "cues": [dict(r) for r in rows],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def delete_transcript(self, track_id, version):
        track = self.track(track_id)
        with self.db(write=True) as db:
            rows = db.execute(
                "SELECT payload FROM cues WHERE track_id=? AND version=? ORDER BY ordinal",
                (track_id, version),
            ).fetchall()
            if not rows:
                raise ValueError("Unknown transcript version")
            directory = self.path / "trash" / uuid.uuid4().hex
            directory.mkdir(parents=True)
            (directory / "transcript.jsonl").write_text(
                "\n".join(r[0] for r in rows) + "\n", encoding="utf-8"
            )
            for result in db.execute(
                "SELECT id FROM translation_versions WHERE track_id=? AND version=?", (track_id, version)
            ).fetchall():
                self._archive_result(db, track_id, version, result[0])
                db.execute("DELETE FROM translation_cues WHERE result_id=?", (result[0],))
                db.execute("DELETE FROM translation_runs WHERE result_id=?", (result[0],))
            # Keep deleted checkpoints for inspection; next ASR must really rerun.
            paths = {
                Path(Cue.model_validate_json(r[0]).provenance["chunk"]["output"]).parent
                for r in rows
                if Cue.model_validate_json(r[0]).provenance.get("chunk", {}).get("output")
            }
            for path in paths:
                if path.is_dir() and path.resolve().is_relative_to(
                    (self.path / "cache" / track_id).resolve()
                ):
                    # Other assembled versions may share these raw checkpoints;
                    # they remain usable as transcripts, and regenerations run fresh.
                    shutil.move(str(path), directory / path.name)
            db.execute("DELETE FROM cues WHERE track_id=? AND version=?", (track_id, version))
            db.execute("DELETE FROM transcript_versions WHERE track_id=? AND version=?", (track_id, version))
            db.execute("DELETE FROM translation_versions WHERE track_id=? AND version=?", (track_id, version))
            db.execute("DELETE FROM active_translations WHERE track_id=? AND version=?", (track_id, version))
            model = Cue.model_validate_json(rows[0][0]).provenance.get("model")
            if model:
                db.execute(
                    "INSERT OR REPLACE INTO settings VALUES (?,?)",
                    (f"asr-generation:{track_id}:{model}", json.dumps(uuid.uuid4().hex)),
                )
            if track.active_version == version:
                other = db.execute(
                    "SELECT version FROM cues WHERE track_id=? ORDER BY rowid DESC LIMIT 1", (track_id,)
                ).fetchone()
                track.active_version = other[0] if other else None
                db.execute("UPDATE tracks SET payload=? WHERE id=?", (track.model_dump_json(), track_id))

    def asr_nonce(self, track_id, model):
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM settings WHERE key=?", (f"asr-generation:{track_id}:{model}",)
            ).fetchone()
            return json.loads(row[0]) if row else None
