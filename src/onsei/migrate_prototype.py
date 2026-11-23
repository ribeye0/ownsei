"""One-time, idempotent import of the Vulkan prototype without changing its files."""

from __future__ import annotations
import json
import shutil
from pathlib import Path
from onsei.audio import import_audio
from onsei.domain import Cue
from onsei.storage import Store
from onsei.util import ROOT, digest, atomic_json


def result_path(run):
    return (
        Path(run["workspace"])
        / "results"
        / run["result_name"]
        / ("asr.json" if run["kind"] == "asr" else "bilingual.json")
    )


def migrate(state_path=None):
    from onsei.api import registry

    state_path = Path(state_path or ROOT / "data/state.json")
    data = json.loads(state_path.read_text())
    if any(j.get("status") in ("queued", "running", "waiting") for j in data.get("jobs", [])):
        raise ValueError("請先停止試用版任務再匯入")
    report = {"projects": 0, "tracks": 0, "transcripts": 0, "translations": 0, "jobs": 0, "warnings": []}
    for project in data["projects"]:
        identity = project["id"]
        destination = ROOT / "workspace" / identity
        with registry() as db:
            if db.execute("SELECT 1 FROM projects WHERE id=?", (identity,)).fetchone():
                continue
        # Incomplete imports never become visible. A subsequent run retries the staging folder.
        staging = destination.with_name(destination.name + ".importing")
        if staging.exists():
            shutil.rmtree(staging)
        store = Store.create(staging, project["title"])
        store.update_settings({"created_at": project["created_at"]})
        track_map = {}
        for old in (t for t in data["tracks"] if t["project_id"] == identity):
            source = Path(old["path"])
            if not source.exists():
                raise ValueError(f"原音檔不存在，未提交遷移：{source}")
            imported = import_audio(store, source)
            track = (
                imported[0]
                if imported
                else next(t for t in store.tracks() if t.source == str(source.resolve()))
            )
            track.name = old["name"]
            store.save_track(track)
            track_map[old["id"]] = track.id
            report["tracks"] += 1
            runs = sorted(old.get("runs", []), key=lambda r: r["created_at"])
            version_cues = {}
            for run in (r for r in runs if r["kind"] == "asr"):
                path = result_path(run)
                if not path.exists():
                    report["warnings"].append(
                        {"run": run["id"], "reason": "No ASR output; original run retained"}
                    )
                    continue
                rows = json.loads(path.read_text())
                if not rows:
                    continue
                machine_path = path.with_name("machine-asr.json")
                machine = (
                    {str(c["id"]): c for c in json.loads(machine_path.read_text())}
                    if machine_path.exists()
                    else {}
                )
                cues = []
                for i, row in enumerate(rows):
                    original = machine.get(str(row["id"]), row)
                    start, end = round(row["start"] * 1000), round(row["end"] * 1000)
                    if end <= start:
                        report["warnings"].append(
                            {
                                "run": run["id"],
                                "cue": row["id"],
                                "reason": "Invalid interval; retained in prototype",
                            }
                        )
                        continue
                    cues.append(
                        Cue(
                            id=f"{track.id}-{run['id']}-{i:05d}",
                            track_id=track.id,
                            version=run["id"],
                            ordinal=i,
                            start_ms=start,
                            end_ms=end,
                            ja_raw=original["ja"],
                            ja_override=row["ja"] if row["ja"] != original["ja"] else None,
                            provenance={
                                "model": "haas-ja-v1.0-f16" if run.get("quant") == "F16" else "haas-ja-v1.0",
                                "revision": "6b965d5007ba780585de51549d57a8c1c0d67c7e",
                                "engine": "speech.cpp",
                                "prototype_run": run,
                                "prototype_cue": str(row["id"]),
                                "processed_duration_ms": min(
                                    track.duration_ms, round(run.get("limit_seconds", 0) * 1000)
                                )
                                if run.get("limit_seconds")
                                else track.duration_ms,
                            },
                        )
                    )
                if not cues:
                    continue
                store.replace_cues(track, run["id"], cues)
                version_cues[run["id"]] = cues
                with store.db(write=True) as db:
                    db.execute(
                        "UPDATE transcript_versions SET created_at=? WHERE track_id=? AND version=?",
                        (run["created_at"], track.id, run["id"]),
                    )
                report["transcripts"] += 1
                if any(c.get("zh") for c in rows):
                    _translation(
                        store,
                        track.id,
                        run["id"],
                        digest({"manual": run["id"]})[:16],
                        "manual",
                        rows,
                        cues,
                        run["created_at"],
                        manual=True,
                    )
                    report["translations"] += 1
            for run in (r for r in runs if r["kind"] == "translation"):
                cues = version_cues.get(run["asr_id"])
                path = result_path(run)
                if not cues or not path.exists():
                    report["warnings"].append(
                        {"run": run["id"], "reason": "Missing source or output; retained in prototype"}
                    )
                    continue
                rows = json.loads(path.read_text())
                _translation(
                    store,
                    track.id,
                    run["asr_id"],
                    run["id"],
                    "index-nativelong-9b",
                    rows,
                    cues,
                    run["created_at"],
                    run=run,
                    output=path,
                )
                report["translations"] += 1
        # Preserve historical task logs as terminal jobs; old-format jobs cannot be resumed as new jobs.
        for old in (j for j in data.get("jobs", []) if j["project_id"] == identity):
            logdir = store.path / "logs"
            logdir.mkdir(exist_ok=True)
            log = state_path.parent / "jobs" / (old["id"] + ".log")
            if log.exists():
                shutil.copy2(log, logdir / log.name)
            job = {
                **old,
                "id": old["id"],
                "action": old.get("action", "run"),
                "options": {
                    "prototype": old,
                    "track_ids": [track_map[old["track_id"]]] if old.get("track_id") in track_map else [],
                },
                "status": old["status"]
                if old["status"] in ("completed", "failed", "cancelled")
                else "failed",
                "message": "試用版紀錄 · " + old.get("message", ""),
                "stages": {},
                "log_sources": [],
                "finished_at": old.get("finished_at", old["created_at"]),
            }
            job.pop("pid", None)
            if job["options"]["track_ids"]:
                track = store.track(job["options"]["track_ids"][0])
                job.update(
                    tracks=[{"id": track.id, "name": track.name}],
                    current_track=track.id,
                    current_track_name=track.name,
                    track_number=1,
                )
            job.update(legacy_progress(job))
            store.save_job(job)
            report["jobs"] += 1
        atomic_json(
            store.path / "prototype-import.json",
            {"source": str(state_path), "project": project, "tracks": track_map},
        )
        # Registry entry is committed only after the complete project directory is durable.
        staging.replace(destination)
        with registry() as db:
            db.execute(
                "INSERT INTO projects VALUES (?,?,?)",
                (identity, str(destination), project["created_at"] if project.get("deleted") else None),
            )
        report["projects"] += 1
    if report["projects"] or not (ROOT / "workspace/prototype-import-report.json").exists():
        atomic_json(ROOT / "workspace/prototype-import-report.json", report)
    return report


def legacy_progress(job):
    old = job["options"]["prototype"]
    ids = job["options"].get("track_ids", [])
    if not ids:
        return {}
    stages = {}
    names = ["transcribe", "translate"] if job["action"] == "run" else [job["action"]]
    for name in names:
        prefix = "asr" if name == "transcribe" else "translation"
        done, total = old.get(prefix + "_done", 0), old.get(prefix + "_total", 0)
        completed = job["status"] == "completed" or bool(total and done >= total)
        stages[name] = {
            "status": "completed" if completed else "failed" if job["status"] == "failed" else "cancelled",
            "progress": 1 if completed else min(1, done / total) if total else 0,
        }
    return {
        "stages": {ids[0]: stages},
        "tracks_total": 1,
        "progress": 1 if job["status"] == "completed" else 0,
    }


def _translation(
    store, track, version, identity, model, rows, cues, created_at, *, manual=False, run=None, output=None
):
    rows = {str(row["id"]): row for row in rows}
    machine_path = output.with_name("machine-bilingual.json") if output else None
    machine = (
        {str(c["id"]): c for c in json.loads(machine_path.read_text())}
        if machine_path and machine_path.exists()
        else {}
    )
    with store.db(write=True) as db:
        db.execute(
            "INSERT INTO translation_versions VALUES (?,?,?,?,?)",
            (identity, track, version, model, created_at),
        )
        for cue in cues:
            key = cue.provenance["prototype_cue"]
            current = rows.get(key, {})
            value = current.get("zh") or None
            original = machine.get(key, current).get("zh") or None
            human = value if manual or value != original else None
            payload = {
                "zh_auto": None if manual else original,
                "zh_override": human,
                "translation_status": "manual"
                if human is not None
                else "translated"
                if original
                else "pending",
                "translation": {"model": model, "prototype_run": run or {}},
                "translation_flags": [],
            }
            db.execute(
                "INSERT INTO translation_cues VALUES (?,?,?)",
                (identity, cue.id, json.dumps(payload, ensure_ascii=False)),
            )
        metadata = {
            "stage": "draft",
            "status": "completed" if all(r.get("zh") for r in rows.values()) else "partial",
            "model": model,
            "scope": "full",
            "target_language": (run or {}).get("target_language", "zh-Hant"),
            "prototype": run,
        }
        db.execute("INSERT INTO translation_runs VALUES (?,?)", (identity, json.dumps(metadata)))
        store._activate_translation(db, track, version, identity)


if __name__ == "__main__":
    print(json.dumps(migrate(), ensure_ascii=False, indent=2))
