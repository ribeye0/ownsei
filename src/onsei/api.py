from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from filelock import FileLock, Timeout as LockTimeout
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from onsei.api_system import router as system_router
from onsei.models import ASR_MODELS, DEFAULT_ASR, DEFAULT_TRANSLATION, TRANSLATION_MODELS
from onsei.audio import import_audio
from onsei.importjobs import latest_import, read_import, start_import
from onsei.export import export_track
from onsei.runner import cancel_job, recover, start_job, resume_job
from onsei.project_paths import project_location
from onsei.storage import Store, ConflictError
from onsei.snapshots import fresh_exports
from onsei.view import track_view
from onsei.util import ROOT
from onsei.joblogs import read_log, stream_log
from onsei.preferences import (
    translation_preferences,
    DEFAULT_STYLES,
    updated_preferences,
    pin_translation_settings,
)
from onsei.terminology import normalize_entries, enabled_terms
from onsei.global_preferences import load_global, save_global

app = FastAPI(title="Ownsei", docs_url="/api/docs", redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False)
REGISTRY = ROOT / "workspace/index.db"


@contextmanager
def registry():
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(REGISTRY)
    # Migrate existing installations without moving user-owned project directories.
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,path TEXT UNIQUE,trashed_at REAL)"
        )
        if "trashed_at" not in {r[1] for r in db.execute("PRAGMA table_info(projects)")}:
            db.execute("ALTER TABLE projects ADD COLUMN trashed_at REAL")
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            workspace = REGISTRY.parent
            for identity, saved in db.execute("SELECT id,path FROM projects").fetchall():
                try:
                    location = project_location(workspace, identity, saved)
                except ValueError:
                    continue  # External registration from a different operating system.
                if location == workspace / identity and location.parent == workspace:
                    db.execute("UPDATE projects SET path=? WHERE id=?", (identity, identity))
            db.execute("PRAGMA user_version=1")
        db.commit()
        with db:
            yield db
    finally:
        db.close()


def registered_project(db, project_id: str, include_trash=False) -> Store:
    row = db.execute("SELECT path,trashed_at FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown project")
    if row[1] is not None and not include_trash:
        raise HTTPException(410, "專案已刪除，請先從垃圾桶還原。")
    return Store(project_location(REGISTRY.parent, project_id, row[0]))


def project(project_id: str) -> Store:
    with registry() as db:
        return registered_project(db, project_id)


app.include_router(system_router)


@app.middleware("http")
async def local_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and origin != f"http://{request.headers.get('host')}":
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "Local same-origin access required"}, status_code=403)
    try:
        return await call_next(request)
    except ConflictError as e:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(e)}, status_code=409)
    except (sqlite3.OperationalError, LockTimeout) as e:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"detail": f"資料或檔案正在被其他操作使用，請稍後重新載入確認：{e}"}, status_code=503
        )
    except ValueError as e:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(e)}, status_code=422)


class CreateProject(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    path: str | None = None
    copy_from: str | None = None
    target_language: str = "zh-Hant"

    @field_validator("target_language")
    @classmethod
    def valid_target(cls, value):
        translation_preferences({"target_language": value})
        return value

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("專案標題不可空白")
        return value


@app.get("/api/projects")
def projects(trashed: bool = False):
    result = []
    with registry() as db:
        rows = db.execute(
            "SELECT id,path,trashed_at FROM projects WHERE (trashed_at IS NOT NULL)=? ORDER BY rowid",
            (trashed,),
        ).fetchall()
    for identity, path, trashed_at in rows:
        try:
            s = Store(project_location(REGISTRY.parent, identity, path))
            recover(s)
            result.append(
                {
                    "id": identity,
                    "path": str(s.path),
                    **s.settings(),
                    "tracks": len(s.tracks()),
                    "trashed_at": trashed_at,
                }
            )
        except ValueError:
            pass
    return result


@app.post("/api/projects")
def create_project(body: CreateProject):
    identity = uuid.uuid4().hex[:12]
    if body.path and body.copy_from:
        raise ValueError("不能同時指定既有目錄與複製來源")
    path = Path(body.path).resolve() if body.path else REGISTRY.parent / identity
    existing_data = (path / "project.db").exists()
    if body.copy_from:
        from onsei.project_copy import copy_project

        s = copy_project(Path(body.copy_from), path)
    else:
        s = Store(path) if existing_data else Store.create(path, body.title)
        if not existing_data:
            s.update_settings(updated_preferences(s.settings(), {"target_language": body.target_language}))
    with registry() as db:
        relative_key = s.path.name if s.path.parent == REGISTRY.parent else None
        existing = db.execute(
            "SELECT id,trashed_at FROM projects WHERE path IN (?,?)", (str(s.path), relative_key)
        ).fetchone()
        if existing:
            if existing[1] is not None:
                raise HTTPException(409, "這個專案已在垃圾桶，請使用還原功能。")
            return {"id": existing[0]}
        db.execute(
            "INSERT INTO projects(id,path) VALUES (?,?)", (identity, str(s.path) if body.path else identity)
        )
    return {"id": identity}


@app.delete("/api/projects/{p}")
def trash_project(p: str):
    with registry() as db:
        # Serialize with job submission so a queued/running task cannot be orphaned.
        db.execute("BEGIN IMMEDIATE")
        s = registered_project(db, p, include_trash=True)
        recover(s)
        with s.db() as data:
            active = any(
                json.loads(r[0])["status"] in ("queued", "waiting", "running", "cancelling")
                for r in data.execute("SELECT payload FROM jobs")
            )
        importing = latest_import(s)
        if importing and importing["status"] == "running":
            raise HTTPException(409, "專案仍在匯入音檔，請等待完成再刪除。")
        if active:
            raise HTTPException(409, "專案仍有排隊或執行中的任務，請先取消或等待完成再刪除。")
        db.execute("UPDATE projects SET trashed_at=COALESCE(trashed_at,?) WHERE id=?", (time.time(), p))
    return {"ok": True}


@app.post("/api/projects/{p}/restore")
def restore_project(p: str):
    with registry() as db:
        db.execute("BEGIN IMMEDIATE")
        registered_project(db, p, include_trash=True)
        db.execute("UPDATE projects SET trashed_at=NULL WHERE id=?", (p,))
    return {"ok": True}


@app.get("/api/preferences/defaults")
def preference_defaults():
    from onsei.translation_policy import DEFAULT_GLOSSARY

    return {
        "asr": DEFAULT_ASR,
        "translation": DEFAULT_TRANSLATION,
        "translation_style": DEFAULT_STYLES["zh-Hant"],
        "translation_styles": DEFAULT_STYLES,
        "target_language": "zh-Hant",
        "glossary_entries": normalize_entries(DEFAULT_GLOSSARY),
    }


@app.get("/api/preferences/global")
def global_preferences():
    return load_global()


class GlobalPreferencesRequest(BaseModel):
    revision: int = Field(ge=0, strict=True)
    glossary_entries: list[dict] | dict


@app.put("/api/preferences/global")
def update_global_preferences(body: GlobalPreferencesRequest):
    return save_global(body.glossary_entries, body.revision)


class NativePickerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["file", "directory"]
    initial: str = Field(default="", max_length=4096)


@app.post("/api/native-picker")
def native_picker(body: NativePickerRequest):
    from onsei.native_picker import choose_path

    try:
        return {"path": choose_path(body.kind, body.initial)}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/local-files")
def local_files(path: str | None = None):
    from onsei.localfiles import browse_audio

    try:
        return browse_audio(path)
    except OSError as error:
        raise HTTPException(422, f"無法讀取目錄：{error}") from error


@app.get("/api/projects/{p}/purge")
def purge_scope(p: str):
    from onsei.localfiles import removal_scope

    with registry() as db:
        s = registered_project(db, p, include_trash=True)
        scope = removal_scope(s, p, ROOT / "workspace")
    return {"scope": scope, "path": str(s.path)}


@app.delete("/api/projects/{p}/purge")
def purge_project(p: str):
    from onsei.localfiles import removal_scope, remove_workspace

    with registry() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT trashed_at FROM projects WHERE id=?", (p,)).fetchone()
        if not row or row[0] is None:
            raise HTTPException(409, "只能永久刪除垃圾桶內的專案")
        s = registered_project(db, p, include_trash=True)
        recover(s)
        importing = latest_import(s)
        if s.active_jobs() or (importing and importing["status"] == "running"):
            raise HTTPException(409, "專案仍有活動任務，請等待或取消後再刪除")
        scope = removal_scope(s, p, ROOT / "workspace")
        if scope == "workspace":
            try:
                remove_workspace(s)
            except OSError as error:
                raise HTTPException(503, f"工作資料清除未完成，請檢查檔案權限後重試：{error}") from error
        db.execute("DELETE FROM projects WHERE id=?", (p,))
    return {"ok": True, "scope": scope}


class EmptyTrashRequest(BaseModel):
    project_ids: list[str] = Field(min_length=1)

    @field_validator("project_ids")
    @classmethod
    def unique_ids(cls, values):
        return list(dict.fromkeys(values))


@app.delete("/api/trash/purge")
def empty_trash(body: EmptyTrashRequest):
    # Pin the confirmation's list; projects trashed afterwards are not included.
    # Each project commits separately so one protected/failed directory does not
    # roll back registrations for workspaces already successfully removed.
    deleted, failed = [], []
    for identity in body.project_ids:
        try:
            result = purge_project(identity)
            deleted.append({"id": identity, "scope": result["scope"]})
        except HTTPException as error:
            failed.append({"id": identity, "error": str(error.detail)})
        except (ValueError, OSError, sqlite3.OperationalError, LockTimeout) as error:
            failed.append({"id": identity, "error": str(error)})
    return {"deleted": deleted, "failed": failed}


@app.get("/api/projects/{p}")
def details(p: str):
    s = project(p)
    value = s.settings()
    return {
        "settings": {
            **value,
            **updated_preferences(value, {}),
            "glossary_entries": normalize_entries(value.get("glossary_entries", value.get("glossary", {}))),
        },
        "tracks": [t.model_dump() for t in s.tracks()],
        "jobs": job_snapshot(s),
        "path": str(s.path),
    }


@app.patch("/api/projects/{p}/settings")
def settings(p: str, body: dict):
    if set(body) - {
        "title",
        "notes",
        "glossary",
        "glossary_entries",
        "translation_style",
        "translation_styles",
        "target_language",
    }:
        raise HTTPException(422, "Unsupported setting")
    if "notes" in body and not isinstance(body["notes"], str):
        raise HTTPException(422, "Notes must be a string")
    if "title" in body:
        try:
            body["title"] = CreateProject(title=body["title"]).title
        except ValueError as e:
            raise HTTPException(422, "專案標題須為 1–200 字，且不可空白。") from e
    if "glossary" in body and (
        not isinstance(body["glossary"], dict)
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in body["glossary"].items())
    ):
        raise HTTPException(422, "Glossary must map Japanese strings to Chinese strings")
    s = project(p)
    if "glossary_entries" in body or "glossary" in body:
        entries = normalize_entries(body.get("glossary_entries", body.get("glossary")))
        body.update(glossary_entries=entries, glossary=enabled_terms(entries))
    body.update(updated_preferences(s.settings(), body))
    s.update_settings(body)
    return {"ok": True}


class ImportRequest(BaseModel):
    path: str


@app.post("/api/projects/{p}/import")
def audio_import(p: str, body: ImportRequest):
    return [t.model_dump() for t in import_audio(project(p), Path(body.path))]


@app.post("/api/projects/{p}/imports")
def begin_import(p: str, body: ImportRequest):
    with registry() as db:
        db.execute("BEGIN IMMEDIATE")
        return start_import(registered_project(db, p), Path(body.path))


@app.get("/api/projects/{p}/imports")
def import_status(p: str):
    return latest_import(project(p))


@app.get("/api/projects/{p}/imports/{identity}")
def import_progress(p: str, identity: str):
    try:
        return read_import(project(p), identity)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/projects/{p}/tracks/{t}/cues")
def cues(p: str, t: str):
    return [{**c.model_dump(), "ja": c.ja, "zh": c.zh} for c in project(p).cues(t)]


@app.get("/api/projects/{p}/tracks/{t}/view")
def result_view(p: str, t: str):
    return track_view(project(p), t)


@app.get("/api/projects/{p}/jobs")
def jobs_view(p: str):
    return job_snapshot(project(p))


def job_snapshot(s: Store):
    recover(s)
    jobs = {j["id"]: j for j in s.jobs()}
    jobs.update({j["id"]: j for j in s.active_jobs()})
    with FileLock(s.path / "logs/heartbeat.lock"):
        for job in jobs.values():
            heartbeat = s.path / "logs" / f"{job['id']}.heartbeat.json"
            try:
                value = json.loads(heartbeat.read_text(encoding="utf-8"))
                if value.get("identity") == job.get("process_identity"):
                    job["heartbeat_age_seconds"] = max(0, time.time() - value["time"])
            except (FileNotFoundError, ValueError, KeyError):
                pass
    result = []
    for job in sorted(jobs.values(), key=lambda j: j["created_at"], reverse=True):
        options = dict(job.get("options", {}))
        snapshots = options.pop("input_snapshots", {})
        options["input_fingerprints"] = {
            track: {k: snapshot.get(k) for k in ("version", "result_id", "source_key", "translation_key")}
            for track, snapshot in snapshots.items()
        }
        result.append({**job, "options": options})
    return result


class EditRequest(BaseModel):
    revision: int
    changes: dict
    translation_version: str | None = None


@app.patch("/api/projects/{p}/cues/{c}")
def edit(p: str, c: str, body: EditRequest):
    s = project(p)
    # Legacy clients omit result ID; their revision still protects translation switches.
    expected = (
        body.translation_version
        if "translation_version" in body.model_fields_set
        else s.cue(c).provenance.get("translation_version")
    )
    result = s.edit(c, body.changes, body.revision, require_active=True, expected_result=expected)
    return {**result.model_dump(), "ja": result.ja, "zh": result.zh}


@app.get("/api/projects/{p}/tracks/{t}/versions")
def versions(p: str, t: str):
    return project(p).transcript_versions(t)


@app.post("/api/projects/{p}/tracks/{t}/versions/{v}")
def activate(p: str, t: str, v: str):
    with idle_project(p) as s:
        if not s.cues(t, v):
            raise HTTPException(404, "Unknown transcript version")
        s.activate_transcript(t, v)
        view = track_view(s, t)
    return {"ok": True, **view}


@contextmanager
def idle_project(p):
    # Same registry transaction as submissions: a deletion/switch cannot race
    # with a queued worker deciding which transcript or result to process.
    with registry() as db:
        db.execute("BEGIN IMMEDIATE")
        s = registered_project(db, p)
        recover(s)
        with s.db() as data:
            active = any(
                json.loads(r[0])["status"] in ("queued", "waiting", "running", "cancelling")
                for r in data.execute("SELECT payload FROM jobs")
            )
        if active:
            raise HTTPException(409, "請先取消或等待專案的任務完成，再切換或刪除版本。")
        yield s


@app.delete("/api/projects/{p}/tracks/{t}/versions/{v}")
def delete_transcript(p: str, t: str, v: str):
    with idle_project(p) as s:
        s.delete_transcript(t, v)
    return {"ok": True}


@app.get("/api/projects/{p}/tracks/{t}/translations")
def translation_versions(p: str, t: str, version: str | None = None):
    return project(p).translation_versions(t, version)


@app.post("/api/projects/{p}/tracks/{t}/translations/{v}")
def activate_translation(p: str, t: str, v: str):
    with idle_project(p) as s:
        s.activate_translation(t, v)
        view = track_view(s, t)
    return {"ok": True, **view}


@app.delete("/api/projects/{p}/tracks/{t}/translations/{v}")
def delete_translation(p: str, t: str, v: str):
    with idle_project(p) as s:
        s.delete_translation(t, v)
    return {"ok": True}


class TranslationOptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context: Literal[8192, 16384, 32768, 65536, 98304, 131072, 262144] | None = None
    names: bool | None = None
    history: bool | None = None


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = "run"
    asr: str = DEFAULT_ASR
    translation: str = DEFAULT_TRANSLATION
    track_ids: list[str] | None = None
    cue_ids: list[str] | None = None
    target_language: str | None = None
    translation_style: str | None = None
    continue_on_error: bool = False
    fresh_asr: bool = False
    translation_options: TranslationOptionsRequest | None = None
    transcript_versions: dict[str, str] | None = None
    asr_filter: dict | None = None
    execution: dict | None = None


@app.post("/api/projects/{p}/jobs")
def new_job(p: str, body: JobRequest):
    if body.action not in ("run", "transcribe", "translate", "export"):
        raise HTTPException(422, "Unknown action")
    if (
        body.action in ("run", "transcribe") and body.asr not in ASR_MODELS
    ) or body.translation not in TRANSLATION_MODELS:
        raise HTTPException(422, "Unknown model")
    options = body.model_dump(exclude={"action"})
    from onsei.backend import normalize_execution

    try:
        options["execution"] = normalize_execution(body.execution)
    except ValueError as error:
        raise HTTPException(422, str(error))
    if body.asr_filter is not None:
        from onsei.asr_filter import normalize_filter

        try:
            normalized = normalize_filter(body.asr_filter)
        except ValueError as error:
            raise HTTPException(422, str(error))
        options["asr_filter"] = {k: v for k, v in normalized.items() if k != "pipeline"}
    if body.translation_options is not None:
        options["translation_options"] = body.translation_options.model_dump(exclude_none=True)
    with registry() as db:
        db.execute("BEGIN IMMEDIATE")
        s = registered_project(db, p)
        if body.track_ids == []:
            raise HTTPException(422, "請至少選擇一條音軌")
        if options["track_ids"] and set(options["track_ids"]) - {t.id for t in s.tracks()}:
            raise HTTPException(422, "Unknown track")
        selected_tracks = body.track_ids or [t.id for t in s.tracks()]
        if not selected_tracks:
            raise HTTPException(422, "專案沒有可處理的音軌")
        overrides = {
            k: options.pop(k) for k in ("target_language", "translation_style") if options.get(k) is not None
        }
        options.pop("target_language", None)
        options.pop("translation_style", None)
        options["translation_settings"] = (
            pin_translation_settings(
                {**s.settings(), **updated_preferences(s.settings(), overrides)},
                load_global()["glossary_entries"],
            )
            if body.action in ("run", "translate")
            else translation_preferences(s.settings())
        )
        if body.transcript_versions is None:
            options.pop("transcript_versions", None)
        else:
            if body.action != "translate" or set(body.transcript_versions) != set(selected_tracks):
                raise HTTPException(422, "翻譯來源須對應全部所選音軌")
            for track_id, version in body.transcript_versions.items():
                if not s.cues(track_id, version):
                    raise HTTPException(422, "所選轉錄不存在，請重新選擇來源")
        if body.action in ("translate",) and any(
            not s.cues(t, (body.transcript_versions or {}).get(t)) for t in selected_tracks
        ):
            raise HTTPException(422, "所選音軌尚無逐字稿，請先轉錄")
        job = s.new_job(body.action, options)
        start_job(s, job["id"])
    return job


@app.post("/api/projects/{p}/jobs/{j}/cancel")
def cancel(p: str, j: str):
    cancel_job(project(p), j)
    return {"ok": True}


@app.post("/api/projects/{p}/jobs/{j}/resume")
def resume(p: str, j: str):
    with registry() as db:
        db.execute("BEGIN IMMEDIATE")
        s = registered_project(db, p)
        old = s.job(j)
        if old["status"] in ("queued", "running", "waiting", "cancelling"):
            raise HTTPException(409, "Job already active")
        job = resume_job(s, old)
        start_job(s, job["id"])
    return job


@app.get("/api/projects/{p}/events")
async def events(p: str, request: Request):
    s = project(p)

    async def stream():
        previous = None
        while not await request.is_disconnected():
            data = json.dumps(s.jobs(), ensure_ascii=False)
            if data != previous:
                yield f"data: {data}\n\n"
                previous = data
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/projects/{p}/jobs/{j}/log")
def job_log(p: str, j: str):
    s = project(p)
    try:
        job = s.job(j)
    except ValueError as e:
        raise HTTPException(404, "Unknown job") from e
    return {"text": read_log(s, job), "status": job["status"], "updated_at": job["updated_at"]}


@app.get("/api/projects/{p}/jobs/{j}/log/download")
def download_job_log(p: str, j: str):
    s = project(p)
    try:
        job = s.job(j)
    except ValueError as e:
        raise HTTPException(404, "Unknown job") from e
    return StreamingResponse(
        stream_log(s, job),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="onsei-job-{job["id"]}.log"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/projects/{p}/tracks/{t}/audio")
@app.head("/api/projects/{p}/tracks/{t}/audio")
def playback(p: str, t: str):
    s = project(p)
    track = s.track(t)
    source = Path(track.source)
    if not source.is_file():
        raise HTTPException(404, "原音檔不存在，請檢查是否已移動或刪除。")
    from onsei.audio import playback_source

    audio = playback_source(s, track)
    return FileResponse(audio, media_type="audio/mpeg" if audio.suffix == ".mp3" else None)


@app.get("/api/projects/{p}/exports")
def exports(p: str):
    return fresh_exports(project(p))


@app.get("/api/projects/{p}/exports/{filename}")
def download(p: str, filename: str):
    s = project(p)
    directory = s.path / "exports"
    target = (directory / filename).resolve()
    if not target.is_file():
        raise HTTPException(404, "Export does not exist")
    if filename not in fresh_exports(s):
        raise HTTPException(409, "Export is stale; generate it again")
    if target.parent != directory.resolve() or not target.is_file():
        raise HTTPException(404, "Export does not exist")
    # Legacy auto-export storage names include version IDs. They remain private
    # identifiers; downloaded attachments use the original audio basename too.
    from onsei.snapshots import result_exports
    from onsei.delivery import audio_stem

    download_name = target.name
    with s.db() as db:
        db.execute("BEGIN")
        for track in s.tracks():
            _, cues = s.read_result(db, track.id)
            if filename in result_exports(db, track.id, cues):
                extension = ".timed.md" if filename.endswith("bilingual-timed.md") else target.suffix
                download_name = audio_stem(track) + extension
                break
    return FileResponse(target, filename=download_name)


@app.post("/api/projects/{p}/export")
def export(p: str, body: dict):
    s = project(p)
    tracks = body.get("track_ids") or [t.id for t in s.tracks()]
    return [
        f.name
        for t in tracks
        for f in export_track(s, t, s.path / "exports", body.get("japanese_only", False))
    ]


class DeliverySelection(BaseModel):
    track_id: str
    version: str | None = None
    result_id: str | None = None


class DeliveryRequest(BaseModel):
    selections: list[DeliverySelection]
    languages: list[str] = ["bilingual"]
    formats: list[str] = ["vtt"]
    chinese: str = "as-result"
    language_tag: bool = False
    destination: str = "download"
    overwrite: bool = False
    clear_cache: bool = False


@app.post("/api/projects/{p}/deliveries")
def delivery(p: str, body: DeliveryRequest):
    from onsei.delivery import prepare_delivery

    return prepare_delivery(
        project(p),
        [v.model_dump() for v in body.selections],
        body.languages,
        body.formats,
        body.chinese,
        language_tag=body.language_tag,
        destination=body.destination,
        overwrite=body.overwrite,
        clear_cache=body.clear_cache,
    )


@app.post("/api/projects/{p}/deliveries/{identity}/clear-cache")
def clear_delivery_cache(p: str, identity: str):
    from onsei.delivery import cleanup_delivery_pcm

    try:
        return cleanup_delivery_pcm(project(p), identity)
    except FileNotFoundError:
        raise HTTPException(404, "找不到匯出紀錄") from None


@app.get("/api/projects/{p}/deliveries/{identity}")
def download_delivery(p: str, identity: str):
    from onsei.delivery import delivery_download

    try:
        path, filename = delivery_download(project(p), identity)
    except FileNotFoundError as error:
        raise HTTPException(404, "匯出下載不存在") from error
    return FileResponse(path, media_type="application/zip", filename=filename)


@app.get("/api/projects/{p}/deliveries/{identity}/files/{index}")
def download_delivery_file(p: str, identity: str, index: int):
    from onsei.delivery import delivery_file

    try:
        path, filename = delivery_file(project(p), identity, index)
    except FileNotFoundError as error:
        raise HTTPException(404, "匯出下載不存在") from error
    return FileResponse(path, filename=filename)


class LocationRequest(BaseModel):
    project: str
    track: str
    transcript: str | None = None
    translation: str | None = None
    cue: str | None = None


@app.post("/api/locations/compact")
def compact_report(body: LocationRequest):
    from onsei.locations import compact_location

    with registry() as db:
        projects = [r[0] for r in db.execute("SELECT id FROM projects")]
        s = registered_project(db, body.project)
        return {"code": compact_location(s, body.model_dump(), projects)}


@app.get("/api/locations/resolve")
def resolve_report(code: str):
    from onsei.locations import resolve_location

    with registry() as db:
        projects = [r[0] for r in db.execute("SELECT id FROM projects")]
        return resolve_location(code, projects, lambda p: registered_project(db, p, include_trash=True))


static = ROOT / "frontend/dist"
if static.exists():
    app.mount("/", StaticFiles(directory=static, html=True), name="ui")
else:

    @app.get("/")
    def not_built():
        return {"message": "Build the frontend: cd frontend && npm ci && npm run build"}
