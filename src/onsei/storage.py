from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from filelock import FileLock

from onsei.domain import Cue, Track
from onsei.versions import VersionStore
from onsei.util import digest
from onsei.translation_contract import dependency_window, polluted


class ConflictError(ValueError):
    """The caller's snapshot no longer matches the result being changed."""


def read_cue(raw: str) -> Cue:
    cue = Cue.model_validate_json(raw)
    # Derived validity for legacy data; preserve the original machine text for audit.
    cue.review_flags = [flag for flag in cue.review_flags if flag != "translation_invalid_output"]
    if cue.zh_override is None and polluted(cue.zh_auto):
        cue.translation_status = "stale"
        cue.review_flags.append("translation_invalid_output")
    return cue


class Store(VersionStore):
    def __init__(self, project: str | Path):
        self.path = Path(project).resolve()
        if not (self.path / "project.db").exists():
            raise ValueError(f"Not an Onsei project: {self.path}")
        with FileLock(self.path / ".schema.lock"):
            self.migrate_versions()

    @classmethod
    def create(cls, path: str | Path, title: str) -> Store:
        p = Path(path).resolve()
        p.mkdir(parents=True, exist_ok=True)
        if (p / "project.db").exists():
            raise ValueError("Project already exists")
        with closing(sqlite3.connect(p / "project.db")) as db, db:
            db.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE settings(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
          CREATE TABLE tracks(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
          CREATE TABLE cues(id TEXT PRIMARY KEY, track_id TEXT NOT NULL, version TEXT NOT NULL,
                            ordinal INTEGER NOT NULL, payload TEXT NOT NULL);
          CREATE INDEX cue_track ON cues(track_id,version,ordinal);
          CREATE TABLE jobs(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
          CREATE TABLE edits(id INTEGER PRIMARY KEY, cue_id TEXT, before TEXT, after TEXT, time REAL);
          PRAGMA user_version=1;
        """)
            db.execute(
                "INSERT INTO settings VALUES (?,?)",
                ("project", json.dumps({"title": title, "glossary": {}, "notes": "", "created_at": time.time()})),
            )
        return cls(p)

    @contextmanager
    def db(self, write=False):
        conn = sqlite3.connect(self.path / "project.db", timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def settings(self) -> dict:
        with self.db() as db:
            return json.loads(db.execute("SELECT payload FROM settings WHERE key='project'").fetchone()[0])

    def update_settings(self, changes: dict) -> None:
        with self.db(write=True) as db:
            old = json.loads(db.execute("SELECT payload FROM settings WHERE key='project'").fetchone()[0])
            from onsei.terminology import project_terms

            merged = {**old, **changes}
            if "glossary" in changes and "glossary_entries" not in changes:
                merged.pop("glossary_entries", None)
            changed = old.get("notes") != merged.get("notes") or project_terms(old) != project_terms(merged)
            old = merged
            db.execute("UPDATE settings SET payload=? WHERE key='project'", (json.dumps(old),))
            if changed:
                db.execute("UPDATE translation_runs SET payload=json_set(payload,'$.status','stale')")
                for row in db.execute("SELECT id,payload FROM cues").fetchall():
                    c = self._read_version_cue(db, row["payload"])
                    if c.zh_override is None:
                        c.translation_status = "stale"
                        c.revision += 1
                    self._write_version_cue(db, c)
                    self._invalidate_versions(db, [c.id])

    def tracks(self) -> list[Track]:
        with self.db() as db:
            return [
                Track.model_validate_json(r[0])
                for r in db.execute("SELECT payload FROM tracks ORDER BY rowid")
            ]

    def track(self, track_id: str) -> Track:
        with self.db() as db:
            row = db.execute("SELECT payload FROM tracks WHERE id=?", (track_id,)).fetchone()
            if not row:
                raise ValueError("Unknown track")
            return Track.model_validate_json(row[0])

    def save_track(self, track: Track) -> None:
        with self.db() as db:
            db.execute(
                "INSERT INTO tracks VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (track.id, track.model_dump_json()),
            )

    def activate_transcript(self, track_id, version):
        with self.db(write=True) as db:
            track, _ = self.read_result(db, track_id)
            if not db.execute(
                "SELECT 1 FROM cues WHERE track_id=? AND version=? LIMIT 1", (track_id, version)
            ).fetchone():
                raise ValueError("Unknown transcript version")
            track.active_version = version
            db.execute("UPDATE tracks SET payload=? WHERE id=?", (track.model_dump_json(), track_id))

    def cues(self, track_id: str, version: str | None = None) -> list[Cue]:
        with self.db() as db:
            db.execute("BEGIN")
            if version is None:
                return self.read_result(db, track_id)[1]
            raws = [
                r[0]
                for r in db.execute(
                    "SELECT payload FROM cues WHERE track_id=? AND version=? ORDER BY ordinal",
                    (track_id, version),
                )
            ]
            return [read_cue(c.model_dump_json()) for c in self.read_version_cues(db, raws)]

    def read_result(self, db, track_id):
        row = db.execute("SELECT payload FROM tracks WHERE id=?", (track_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown track")
        track = Track.model_validate_json(row[0])
        raws = [
            r[0]
            for r in db.execute(
                "SELECT payload FROM cues WHERE track_id=? AND version=? ORDER BY ordinal",
                (track_id, track.active_version),
            )
        ]
        return track, [read_cue(c.model_dump_json()) for c in self.read_version_cues(db, raws)]

    def result(self, track_id):
        with self.db() as db:
            db.execute("BEGIN")
            return self.read_result(db, track_id)

    def cue(self, cue_id: str) -> Cue:
        with self.db() as db:
            row = db.execute("SELECT payload FROM cues WHERE id=?", (cue_id,)).fetchone()
            if not row:
                raise ValueError("Unknown cue")
            return read_cue(self._read_version_cue(db, row[0]).model_dump_json())

    def replace_cues(self, track: Track, version: str, cues: list[Cue]) -> None:
        with self.db(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO transcript_versions VALUES (?,?,?,0)", (track.id, version, time.time())
            )
            db.execute("DELETE FROM cues WHERE track_id=? AND version=?", (track.id, version))
            db.executemany(
                "INSERT INTO cues VALUES (?,?,?,?,?)",
                [(c.id, c.track_id, c.version, c.ordinal, c.model_dump_json()) for c in cues],
            )
            track.active_version = version
            current = Track.model_validate_json(
                db.execute("SELECT payload FROM tracks WHERE id=?", (track.id,)).fetchone()[0]
            )
            current.active_version = version
            db.execute("UPDATE tracks SET payload=? WHERE id=?", (current.model_dump_json(), track.id))

    def save_generated_batch(self, cues):
        # Direct store callers retain their per-cue optimistic revision contract.
        return {cue.id for cue in cues if self.save_generated(cue, cue.revision)}

    def save_generated(self, cue: Cue, expected_revision: int) -> bool:
        with self.db(write=True) as db:
            row = db.execute("SELECT payload FROM cues WHERE id=?", (cue.id,)).fetchone()
            if not row:
                return False
            current = self._read_version_cue(db, row[0])
            if current.revision != expected_revision or cue.provenance.get(
                "translation_version"
            ) != current.provenance.get("translation_version"):
                return False
            translation = cue.provenance.get("translation") or {}
            if cue.translation_status == "translated" and translation.get("document_key"):
                source = [
                    read_cue(r[0])
                    for r in db.execute(
                        "SELECT payload FROM cues WHERE track_id=? AND version=? ORDER BY ordinal",
                        (cue.track_id, cue.version),
                    )
                ]
                document = [{"id": str(c.ordinal + 1), "text": c.ja} for c in source]
                if digest(document) != translation["document_key"]:
                    return False
            cue.ja_override, cue.zh_override = current.ja_override, current.zh_override
            cue.revision = current.revision + 1
            self._write_version_cue(db, cue)
            return True

    def edit(
        self,
        cue_id: str,
        changes: dict,
        expected_revision: int | None = None,
        *,
        require_active=False,
        expected_result=None,
    ) -> Cue:
        changes = dict(changes)
        allowed = {"ja_override", "zh_override", "start_ms", "end_ms", "review_flags"}
        if set(changes) - allowed:
            raise ValueError("Unsupported cue fields")
        with self.db(write=True) as db:
            row = db.execute("SELECT payload FROM cues WHERE id=?", (cue_id,)).fetchone()
            if not row:
                raise ValueError("Unknown cue")
            before = self._read_version_cue(db, row[0])
            if expected_revision is not None and before.revision != expected_revision:
                raise ConflictError("校對內容已更新，請保留草稿並重新載入比對後再儲存。")
            after = Cue.model_validate({**before.model_dump(), **changes, "revision": before.revision + 1})
            track = Track.model_validate_json(
                db.execute("SELECT payload FROM tracks WHERE id=?", (before.track_id,)).fetchone()[0]
            )
            if require_active and (
                track.active_version != before.version
                or before.provenance.get("translation_version") != expected_result
            ):
                raise ConflictError("目前結果版本已切換，請保留草稿並重新載入比對後再儲存。")
            if after.end_ms > track.duration_ms:
                raise ValueError("Cue ends beyond audio")
            if after.ja != before.ja:
                # Native ASR timing survives a text correction; only the translation becomes stale.
                after.translation_status = "manual" if after.zh_override is not None else "stale"
                # Invalidate every batch that can read the changed Japanese cue,
                # including adaptive sub-batches and inference already in flight.
                for r in db.execute(
                    "SELECT payload FROM cues WHERE track_id=? AND version=?", (after.track_id, after.version)
                ).fetchall():
                    neighbor = self._read_version_cue(db, r[0])
                    lo, hi = dependency_window(neighbor.ordinal)
                    if lo <= after.ordinal < hi and neighbor.id != cue_id:
                        if neighbor.zh_override is None:
                            neighbor.translation_status = "stale"
                        neighbor.revision += 1
                        self._write_version_cue(db, neighbor)
                        self._invalidate_versions(db, [neighbor.id])
            if "zh_override" in changes and after.zh_override != before.zh_override:
                after.translation_status = "manual" if after.zh_override is not None else "stale"
            if (after.start_ms, after.end_ms) != (before.start_ms, before.end_ms):
                after.timing_status = "manual"
            self._write_version_cue(db, after)
            if after.ja != before.ja or (after.start_ms, after.end_ms) != (before.start_ms, before.end_ms):
                db.execute(
                    """UPDATE translation_runs SET payload=json_set(payload,'$.status','stale')
                    WHERE result_id IN (SELECT id FROM translation_versions WHERE track_id=? AND version=?)""",
                    (after.track_id, after.version),
                )
            elif after.zh != before.zh:
                self._invalidate_history_translations(
                    db, before.provenance.get("translation_version"), after.ordinal
                )
                db.execute(
                    """WITH RECURSIVE descendants(id) AS (
                    SELECT result_id FROM translation_runs WHERE json_extract(payload,'$.parent_result_id')=?
                    UNION SELECT r.result_id FROM translation_runs r JOIN descendants d
                    ON json_extract(r.payload,'$.parent_result_id')=d.id)
                    UPDATE translation_runs SET payload=json_set(payload,'$.status','stale')
                    WHERE result_id IN (SELECT id FROM descendants)""",
                    (before.provenance.get("translation_version"),),
                )
            if after.ja != before.ja:
                self._invalidate_versions(db, [cue_id])
                self._invalidate_document_translations(db, after.track_id, after.version)
            db.execute(
                "INSERT INTO edits(cue_id,before,after,time) VALUES (?,?,?,?)",
                (cue_id, before.model_dump_json(), after.model_dump_json(), time.time()),
            )
            return read_cue(after.model_dump_json())

    def new_job(self, action: str, options: dict) -> dict:
        from copy import deepcopy

        options = deepcopy(options)
        if action in ("run", "translate"):
            from onsei.context import translation_options

            options["translation_options"] = translation_options(
                {"translation_options": options.get("translation_options") or {}},
                options.get("translation", "index-nativelong-9b"),
            )
        from onsei.preferences import translation_preferences, pin_translation_settings, updated_preferences

        if options.get("track_ids") == []:
            raise ValueError("請至少選擇一條音軌")
        project_settings = self.settings()
        overrides = options.get("translation_settings", {})
        settings = {**project_settings, **updated_preferences(project_settings, overrides), **overrides}
        if action in ("run", "translate"):
            from onsei.terminology import normalize_entries, enabled_terms

            entries = normalize_entries(
                project_settings.get("glossary_entries", project_settings.get("glossary", {}))
            )
            options.setdefault(
                "translation_context",
                {
                    "notes": project_settings.get("notes", ""),
                    "glossary_entries": entries,
                    "glossary": enabled_terms(entries),
                },
            )
        options["translation_settings"] = (
            pin_translation_settings(settings)
            if action in ("run", "translate")
            else translation_preferences(settings)
        )
        if action in ("run", "transcribe") and options.get("fresh_asr"):
            options.setdefault(
                "asr_generations",
                {
                    t.id: uuid.uuid4().hex
                    for t in self.tracks()
                    if options.get("track_ids") is None or t.id in options["track_ids"]
                },
            )
        if action in ("translate", "export"):
            options.setdefault(
                "transcript_versions",
                {
                    t.id: t.active_version
                    for t in self.tracks()
                    if not options.get("track_ids") or t.id in options["track_ids"]
                },
            )
            if action in ("export",):
                with self.db() as db:
                    options.setdefault(
                        "translation_versions",
                        {
                            t: self._active_translation(db, t, v)
                            for t, v in options["transcript_versions"].items()
                        },
                    )
            if action in ("translate",):
                options.setdefault(
                    "input_snapshots",
                    {
                        t: self.input_snapshot(t, v)
                        for t, v in options["transcript_versions"].items()
                        if v and self.cues(t, v)
                    },
                )
        for snapshot in options.get("input_snapshots", {}).values():
            snapshot["settings"] = {**snapshot["settings"], **options["translation_settings"]}
            if options.get("translation_options"):
                snapshot["settings"]["translation_options"] = options["translation_options"]
        job = {
            "id": uuid.uuid4().hex,
            "action": action,
            "options": options,
            "status": "queued",
            "progress": 0,
            "message": "Waiting",
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.save_job(job)
        return job

    def save_job(self, job: dict) -> None:
        job["updated_at"] = time.time()
        with self.db(write=True) as db:
            previous = db.execute("SELECT payload FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if previous:
                current = json.loads(previous[0])
                # A worker's stale in-memory status must not undo a cancellation.
                if current["status"] in ("cancelling", "cancelled") and job["status"] in (
                    "queued",
                    "waiting",
                    "running",
                    "completed",
                    "failed",
                ):
                    job.update(status=current["status"], message=current["message"])
                if current.get("cancel_requested_at"):
                    job["cancel_requested_at"] = current["cancel_requested_at"]
            db.execute(
                "INSERT INTO jobs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (job["id"], json.dumps(job)),
            )

    def job(self, job_id: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise ValueError("Unknown job")
            return json.loads(row[0])

    def jobs(self) -> list[dict]:
        with self.db() as db:
            return [
                json.loads(r[0]) for r in db.execute("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 50")
            ]

    def active_jobs(self) -> list[dict]:
        with self.db() as db:
            return [
                job
                for row in db.execute("SELECT payload FROM jobs ORDER BY rowid")
                if (job := json.loads(row[0]))["status"] in ("queued", "waiting", "running", "cancelling")
            ]
