"""Immutable inference inputs and explicitly addressed translation results."""

from __future__ import annotations

import json
import time
import uuid

from onsei.domain import Cue
from onsei.util import digest
from onsei.translation_records import compact_snapshot, intern_batch, store_payload, translation_values
from onsei.preferences import translation_preferences
from onsei.terminology import project_terms


def source_key(snapshot: dict) -> str:
    return digest(
        {
            "track": snapshot["track"]["id"],
            "version": snapshot["version"],
            "settings": {
                "notes": snapshot["settings"].get("notes"),
                "glossary": project_terms(snapshot["settings"]),
            },
            "cues": [
                (c["id"], Cue.model_validate(c).ja, c["start_ms"], c["end_ms"]) for c in snapshot["cues"]
            ],
        }
    )


def run_summary(value):
    return {
        **{
            k: value[k]
            for k in (
                "stage",
                "scope",
                "status",
                "parent_result_id",
                "model_revision",
                "prompt_version",
                "target_language",
                "translation_style",
                "translation_options",
            )
            if k in value
        },
        "reviewed": len(value.get("coverage", [])) if value.get("stage") == "review" else 0,
        "patch_count": sum(p.get("applied", False) for p in value.get("patches", [])),
        "issue_count": len(value.get("issues", [])),
    }


class TranslationRuns:
    def run_metadata(self, identity, db=None):
        if db is None:
            with self.db() as connection:
                return self.run_metadata(identity, connection)
        row = db.execute("SELECT payload FROM translation_runs WHERE result_id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else {"stage": "draft", "status": "legacy"}

    def update_run(self, identity, **changes):
        with self.db(write=True) as db:
            if not db.execute("SELECT 1 FROM translation_versions WHERE id=?", (identity,)).fetchone():
                raise ValueError("Translation result was deleted")
            value = self.run_metadata(identity, db)
            value.update(changes, updated_at=time.time())
            db.execute(
                "INSERT OR REPLACE INTO translation_runs VALUES (?,?)",
                (identity, json.dumps(value, ensure_ascii=False)),
            )

    def input_snapshot(self, track_id, version=None, result_id=None, db=None, *, diagnostics=True):
        if db is None:
            self.ensure_legacy_result(track_id)
            with self.db() as connection:
                connection.execute("BEGIN")
                return self.input_snapshot(track_id, version, result_id, connection, diagnostics=diagnostics)
        track = json.loads(db.execute("SELECT payload FROM tracks WHERE id=?", (track_id,)).fetchone()[0])
        version = version or track["active_version"]
        settings = json.loads(db.execute("SELECT payload FROM settings WHERE key='project'").fetchone()[0])
        if result_id is None:
            result_id = self._active_translation(db, track_id, version)
        if (
            result_id
            and not db.execute(
                "SELECT 1 FROM translation_versions WHERE id=? AND track_id=? AND version=?",
                (result_id, track_id, version),
            ).fetchone()
        ):
            raise ValueError("Source translation no longer exists")
        cues = [
            Cue.model_validate_json(r[0])
            for r in db.execute(
                "SELECT payload FROM cues WHERE track_id=? AND version=? ORDER BY ordinal",
                (track_id, version),
            )
        ]
        if not cues:
            raise ValueError("No Japanese transcript")
        values = translation_values(db, result_id, diagnostics=diagnostics) if result_id else {}
        for cue in cues:
            if result_id:
                value = values.get(cue.id, {})
                cue.zh_auto, cue.zh_override = value.get("zh_auto"), value.get("zh_override")
                cue.translation_status = value.get("translation_status", "pending")
                cue.provenance["translation_version"] = result_id
                if value.get("translation"):
                    cue.provenance["translation"] = value["translation"]
                if value.get("review"):
                    cue.provenance["review"] = value["review"]
                cue.provenance.pop("training", None)
                if value.get("training"):
                    cue.provenance["training"] = value["training"]
                cue.review_flags += value.get("translation_flags", [])
        snapshot = {
            "track": track,
            "version": version,
            "settings": settings,
            "result_id": result_id,
            "cues": [c.model_dump() for c in cues],
        }
        snapshot["source_key"] = source_key(snapshot)
        snapshot["translation_key"] = digest(
            [(c.id, c.zh, c.zh_override, c.translation_status) for c in cues]
        )
        return snapshot

    def snapshot_valid(self, snapshot, db=None, *, include_translation=False):
        try:
            current = self.input_snapshot(
                snapshot["track"]["id"],
                snapshot["version"],
                snapshot["result_id"] if include_translation else None,
                db,
            )
        except (ValueError, TypeError):
            return False
        return current["source_key"] == snapshot["source_key"] and (
            not include_translation or current["translation_key"] == snapshot["translation_key"]
        )

    def new_translation_run(self, snapshot, model, *, stage="draft", identity=None, selected=None):
        identity = identity or uuid.uuid4().hex[:16]
        with self.db(write=True) as db:
            if not self.snapshot_valid(snapshot, db, include_translation=stage == "review"):
                raise ValueError("來源已更新或刪除，請重新提交任務")
            track_id, version = snapshot["track"]["id"], snapshot["version"]
            if db.execute("SELECT 1 FROM translation_versions WHERE id=?", (identity,)).fetchone():
                metadata = self.run_metadata(identity, db)
                if metadata.get("status") == "stale":
                    raise ValueError("來源已更新，過期檢查點不可續跑，請另建翻譯或審校")
                if (
                    metadata.get("source_key") != snapshot["source_key"]
                    or metadata.get("stage") != stage
                    or metadata.get("model") != model
                    or metadata.get("selected_ids", []) != sorted(selected or [])
                    or translation_preferences(metadata) != translation_preferences(snapshot["settings"])
                    or (
                        stage == "review"
                        and metadata.get("source_snapshot", {}).get("translation_key")
                        != snapshot["translation_key"]
                    )
                ):
                    raise ValueError("Checkpoint input does not match this task")
                return identity
            metadata = {
                "stage": stage,
                "status": "pending",
                "parent_result_id": snapshot["result_id"],
                "source_key": snapshot["source_key"],
                "source_snapshot": compact_snapshot(snapshot),
                "coverage": [],
                "issues": [],
                "patches": [],
                "model": model,
                "scope": "selected" if selected else "full",
                "selected_ids": sorted(selected or []),
                **translation_preferences(snapshot["settings"]),
            }
            db.execute(
                "INSERT INTO translation_versions VALUES (?,?,?,?,?)",
                (identity, track_id, version, model, time.time()),
            )
            db.execute(
                "INSERT INTO translation_runs VALUES (?,?)",
                (identity, json.dumps(metadata, ensure_ascii=False)),
            )
            for raw in snapshot["cues"]:
                cue = Cue.model_validate(raw)
                if stage == "draft" and not selected:
                    cue.zh_auto = None
                    cue.translation_status = "manual" if cue.zh_override is not None else "pending"
                    cue.provenance.pop("translation", None)
                    cue.provenance.pop("review", None)
                    cue.review_flags = [f for f in cue.review_flags if not f.startswith("translation_")]
                db.execute(
                    "INSERT INTO translation_cues VALUES (?,?,?)",
                    (identity, cue.id, store_payload(db, identity, self._zh_payload(cue))),
                )
            # Do not steal the user's current view if they switched while queued.
            track = json.loads(db.execute("SELECT payload FROM tracks WHERE id=?", (track_id,)).fetchone()[0])
            if (
                track["active_version"] == version
                and self._active_translation(db, track_id, version) == snapshot["result_id"]
            ):
                self._activate_translation(db, track_id, version, identity)
        return identity


class TranslationSession:
    """Store facade: all generated writes target one run, independent of active UI selection."""

    def __init__(self, store, snapshot, identity):
        self.store, self.snapshot, self.identity = store, snapshot, identity
        self.path = store.path

    def settings(self):
        return self.snapshot["settings"]

    def track(self, track_id):
        from onsei.domain import Track

        return Track.model_validate({**self.snapshot["track"], "active_version": self.snapshot["version"]})

    def prepare_translation(self, *_):
        return self.identity

    def translation_nonce(self, *_):
        return self.identity

    def cues(self, *_):
        with self.store.db() as db:
            if not db.execute("SELECT 1 FROM translation_versions WHERE id=?", (self.identity,)).fetchone():
                raise ValueError("Translation result was deleted")
            values = translation_values(db, self.identity)
            cues = []
            for raw in self.snapshot["cues"]:
                cue = Cue.model_validate(raw)
                value = values.get(cue.id, {})
                cue.zh_auto, cue.zh_override = value.get("zh_auto"), value.get("zh_override")
                cue.translation_status = value.get("translation_status", "pending")
                cue.provenance.pop("translation", None)
                if value.get("translation"):
                    cue.provenance["translation"] = value["translation"]
                cue.provenance.pop("review", None)
                if value.get("review"):
                    cue.provenance["review"] = value["review"]
                cue.review_flags = [
                    f for f in cue.review_flags if not f.startswith("translation_")
                ] + value.get("translation_flags", [])
                cue.provenance["translation_version"] = self.identity
                cue.provenance.pop("training", None)
                if value.get("training"):
                    cue.provenance["training"] = value["training"]
                cues.append(cue)
            return cues

    def save_generated(self, cue, _revision):
        return cue.id in self.save_generated_batch([cue])

    def save_generated_batch(self, cues):
        if not cues:
            return set()
        track_id, version = self.snapshot["track"]["id"], self.snapshot["version"]
        saved = set()
        with self.store.db(write=True) as db:
            # Project setting changes and result deletion can still invalidate a run.
            run = db.execute(
                "SELECT json_extract(payload, '$.status') FROM translation_runs WHERE result_id=?",
                (self.identity,),
            ).fetchone()
            if not run:
                raise ValueError("Translation result was deleted")
            if run[0] == "stale":
                raise ValueError("來源已更新，停止保存過期結果")
            last_metadata = batch_ref = None
            for cue in cues:
                payload = self.store._zh_payload(cue)
                metadata = payload.get("translation")
                if metadata is not last_metadata:
                    last_metadata = metadata
                    batch_ref = intern_batch(db, self.identity, metadata) if metadata else None
                value = store_payload(db, self.identity, payload, batch_ref)
                if db.execute(
                    """UPDATE translation_cues SET payload=? WHERE result_id=? AND cue_id=?
                       AND json_extract(payload, '$.zh_override') IS NULL""",
                    (value, self.identity, cue.id),
                ).rowcount:
                    saved.add(cue.id)
                elif not db.execute(
                    "SELECT 1 FROM translation_cues WHERE result_id=? AND cue_id=?", (self.identity, cue.id)
                ).fetchone():
                    raise ValueError("Translation result was deleted")
            if self.store._active_translation(db, track_id, version) == self.identity:
                db.executemany(
                    """UPDATE cues SET payload=json_set(payload, '$.revision',
                           COALESCE(json_extract(payload, '$.revision'), 0) + 1) WHERE id=?""",
                    ((cue_id,) for cue_id in saved),
                )
        return saved
