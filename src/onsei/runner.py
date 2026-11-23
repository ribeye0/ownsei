from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import traceback
import threading
from pathlib import Path

from filelock import FileLock, Timeout

from onsei.models import ASR_MODELS, DEFAULT_ASR, DEFAULT_TRANSLATION
from onsei.metrics import GpuMetrics
from onsei.storage import Store
from onsei.util import STATE, atomic_json, gpu_lock_path
from onsei.user_paths import locks_dir
from onsei.processes import alive, process_identity, runner_owned, spawn_options, signal_group
from onsei.snapshots import cue_digest


def run_job(store: Store, job_id: str):
    from onsei.backend import execution_scope

    with execution_scope(store.job(job_id)["options"].get("execution")):
        return _run_job(store, job_id)


def _run_job(store: Store, job_id: str):
    job = store.job(job_id)
    if job["status"] in ("cancelled", "cancelling"):
        return
    STATE.mkdir(parents=True, exist_ok=True)
    job.update(
        status="waiting", pid=os.getpid(), process_identity=process_identity(os.getpid()), message="等待 GPU"
    )
    store.save_job(job)

    heartbeat_stop = threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(2):
            # Windows readers must close their handle before the file is replaced.
            with FileLock(store.path / "logs/heartbeat.lock"):
                atomic_json(
                    store.path / "logs" / f"{job_id}.heartbeat.json",
                    {"time": time.time(), "pid": os.getpid(), "identity": job["process_identity"]},
                )

    monitor = threading.Thread(target=heartbeat, daemon=True)
    monitor.start()

    def progress(event):
        if store.job(job_id)["status"] in ("cancelled", "cancelling"):
            raise InterruptedError("Cancelled")
        if "log_source" in event:
            path = Path(event["log_source"]).resolve()
            if path.is_relative_to(store.path / "cache"):
                relative = str(path.relative_to(store.path))
                for previous in job.get("log_sources", []):
                    if previous["path"] == relative and "end" not in previous:
                        previous["end"] = event.get("log_start", 0)
                job.setdefault("log_sources", []).append(
                    {"path": relative, "start": event.get("log_start", 0)}
                )
        if "outputs" in event:
            job["options"].setdefault("processing_outputs", {})[tracks[index].id] = event["outputs"]
        if "pipeline_stage" in event:
            job["pipeline_stage"] = event["pipeline_stage"]
        job["message"] = str(event.get("message", event.get("completed", "處理中")))
        job["last_event"] = event
        if "fraction" in event:
            stage = job.get("current_stage")
            stage_fraction = min(1, max(0, event["fraction"]))
            if stage in weights:
                record = job.setdefault("stages", {}).setdefault(tracks[index].id, {}).setdefault(stage, {})
                record["progress"] = max(record.get("progress", 0), stage_fraction)
            keys = list(weights)
            offset = sum(weights[k] for k in keys[: keys.index(stage)]) if stage in weights else 0
            fraction = offset + weights.get(stage, 1) * stage_fraction
            job["track_progress"] = max(job.get("track_progress", 0), fraction)
            job["progress"] = max(job["progress"], (index + fraction) / len(tracks))
        store.save_job(job)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    try:
        lock_path = gpu_lock_path(locks_dir())
        print(f"GPU lock: {lock_path}", flush=True)
        lock = FileLock(lock_path)
        while True:
            if store.job(job_id)["status"] in ("cancelled", "cancelling"):
                raise InterruptedError("Cancelled")
            recover(store)
            earlier = any(
                j["id"] != job_id
                and j["created_at"] < job["created_at"]
                and j["status"] in ("queued", "waiting", "running", "cancelling")
                for j in store.active_jobs()
            )
            if earlier:
                time.sleep(0.2)
                continue
            try:
                lock.acquire(timeout=0.5)
                break
            except Timeout:
                if store.job(job_id)["status"] in ("cancelled", "cancelling"):
                    raise InterruptedError("Cancelled")
        metrics = GpuMetrics()
        metrics_started = False
        try:
            metrics.__enter__()
            metrics_started = True
            job.update(status="running", started_at=time.time())
            store.save_job(job)
            options = job["options"]
            tracks = [
                t for t in store.tracks() if not options.get("track_ids") or t.id in options["track_ids"]
            ]
            if not tracks:
                raise ValueError("No tracks selected")
            action = job["action"]
            weights = (
                {"transcribe": 0.55, "translate": 0.4, "export": 0.05}
                if action == "run"
                else {"transcribe": 0.95, "export": 0.05}
                if action == "transcribe"
                else {"translate": 0.95, "export": 0.05}
                if action == "translate"
                else {"export": 1}
            )
            job["tracks"] = [{"id": t.id, "name": t.name} for t in tracks]
            job["tracks_total"] = len(tracks)
            job["track_outcomes"] = {}

            def process_track(track):
                if action in ("run", "transcribe"):
                    from onsei.source import validate_source

                    validate_source(store, track)

                valid_resume = job.get("resume", {}).get("snapshots", {}).get(track.id) == result_fingerprint(
                    store, track.id
                )
                pinned = options.get("input_snapshots", {}).get(track.id)
                if (
                    action in ("translate",)
                    and pinned
                    and not store.snapshot_valid(pinned, include_translation=False)
                ):
                    raise ValueError("排隊／續跑的來源已更新，請重新提交任務；既有成果保留")
                if (
                    action not in ("translate",)
                    and not valid_resume
                    and track.id in options.get("transcript_versions", {})
                ):
                    version = options["transcript_versions"][track.id]
                    if not version or not store.cues(track.id, version):
                        raise ValueError("Queued transcript no longer exists; submit a new job")
                    store.activate_transcript(track.id, version)
                    result = options.get("translation_versions", {}).get(track.id)
                    if result:
                        store.activate_translation(track.id, result)
                old_resume = job.get("resume", {})
                resume_valid = old_resume.get("snapshots", {}).get(track.id) == result_fingerprint(
                    store, track.id
                )
                previous_stages = old_resume.get("stages", {}).get(track.id, {}) if resume_valid else {}

                def stage(name, execute):
                    job["current_stage"], job["current_track"] = name, track.id
                    record = job.setdefault("stages", {}).setdefault(track.id, {})
                    if previous_stages.get(name, {}).get("status") == "completed":
                        record[name] = {
                            **previous_stages[name],
                            "progress": 1,
                            "resumed_from": old_resume["job_id"],
                        }
                        progress({"message": f"{track.name}: 保留已完成的 {name} 階段", "fraction": 1})
                        return
                    record[name] = {"status": "running", "progress": 0, "started_at": time.time()}
                    progress({"message": f"{track.name}: {name}"})
                    try:
                        execute()
                    except BaseException:
                        record[name].update(
                            status="cancelled"
                            if store.job(job_id)["status"] in ("cancelling", "cancelled")
                            else "failed",
                            finished_at=time.time(),
                        )
                        job["failed_stage"], job["failed_track"] = name, track.id
                        raise
                    record[name].update(status="completed", progress=1, finished_at=time.time())
                    current = store.cues(track.id)
                    record[name]["review_cues"] = sum(
                        "alignment_native_fallback" in c.review_flags for c in current
                    )
                    job.setdefault("resume_snapshots", {})[track.id] = result_fingerprint(store, track.id)
                    progress({"message": f"{track.name}: {name} 完成", "fraction": 1})

                if action in ("transcribe", "run"):
                    from onsei.asr import transcribe

                    stage(
                        "transcribe",
                        lambda: transcribe(
                            store,
                            track.id,
                            options.get("asr", DEFAULT_ASR),
                            options.get("limit_ms"),
                            progress,
                            **({"asr_filter": options["asr_filter"]} if options.get("asr_filter") else {}),
                            **(
                                {"generation": options["asr_generations"][track.id]}
                                if track.id in options.get("asr_generations", {})
                                else {}
                            ),
                        ),
                    )
                if action in ("translate", "run"):
                    from onsei.translation_pipeline import process_translation

                    stage(
                        "translate",
                        lambda: process_translation(
                            store,
                            track.id,
                            options.get("translation", DEFAULT_TRANSLATION),
                            progress,
                            snapshot=options.get("input_snapshots", {}).get(track.id)
                            if action != "run"
                            else None,
                            selected=options.get("cue_ids"),
                            outputs=options.get("processing_outputs", {}).get(track.id),
                            translation_settings=options.get("translation_settings"),
                            translation_options=options.get("translation_options"),
                            translation_context=options.get("translation_context")
                            if action == "run"
                            else None,
                        ),
                    )
                if action in ("transcribe", "translate", "run", "export"):
                    from onsei.export import export_track

                    output = options.get("processing_outputs", {}).get(track.id, {})
                    result_id = output.get("draft")

                    stage(
                        "export",
                        lambda: export_track(
                            store,
                            track.id,
                            store.path / "exports",
                            japanese_only=action == "transcribe",
                            languages=["ja"] if action == "transcribe" else ["zh"],
                            formats=["vtt"],
                            version=options.get("input_snapshots", {}).get(track.id, {}).get("version")
                            if result_id
                            else None,
                            result_id=result_id,
                        ),
                    )

            for index, track in enumerate(tracks):
                job.update(
                    current_track=track.id,
                    current_track_name=track.name,
                    track_number=index + 1,
                    track_progress=0,
                    current_stage=None,
                )
                store.save_job(job)
                try:
                    process_track(track)
                    job["track_outcomes"][track.id] = {"status": "completed"}
                except (ValueError, RuntimeError) as error:
                    systemic = any(
                        word in str(error).lower()
                        for word in (
                            "model missing",
                            "llama-server",
                            "worker failed",
                            "worker timed out",
                            "cuda",
                            "out of memory",
                            "unsupported",
                            "載入模型",
                            "沒有進展",
                            "connection refused",
                            "connection reset",
                            "timed out",
                        )
                    )
                    if not options.get("continue_on_error") or systemic:
                        raise
                    job["track_outcomes"][track.id] = {"status": "failed", "error": str(error)}
                    print(traceback.format_exc(), flush=True)
                job["progress"] = (index + 1) / len(tracks)
                store.save_job(job)
            failed = sum(v["status"] == "failed" for v in job["track_outcomes"].values())
            job.update(
                status="partial" if failed else "completed",
                message=f"完成 {len(tracks) - failed}/{len(tracks)} 條音軌"
                + (f"；失敗 {failed} 條，可恢復未完成項目" if failed else ""),
                finished_at=time.time(),
            )
        finally:
            try:
                if metrics_started:
                    metrics.__exit__()
                job["metrics"] = metrics.data
            finally:
                lock.release()
    except (InterruptedError, KeyboardInterrupt):
        job.update(status="cancelled", message="已取消；已完成片段保留")
    except Exception as e:
        job.update(
            status="partial"
            if any(
                s.get("status") == "completed"
                for stages in job.get("stages", {}).values()
                for s in stages.values()
            )
            else "failed",
            message=str(e),
            error=traceback.format_exc(),
        )
    heartbeat_stop.set()
    monitor.join(timeout=3)
    # Refresh the final result dependency after partially completed stages.
    job["resume_snapshots"] = {
        t.id: result_fingerprint(store, t.id) for t in store.tracks() if t.id in job.get("stages", {})
    }
    job.setdefault("finished_at", time.time())
    for source in job.get("log_sources", []):
        path = store.path / source["path"]
        if path.is_file():
            source.setdefault("end", path.stat().st_size)
    if job.get("error"):
        print(job["error"], flush=True)
    print(json.dumps({"status": job["status"], "message": job["message"]}, ensure_ascii=False), flush=True)
    store.save_job(job)


def start_job(store: Store, job_id: str):
    logs = store.path / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / f"{job_id}.log").open("a") as log:
        child = subprocess.Popen(
            [sys.executable, "-m", "onsei.runner", str(store.path), job_id],
            stdout=log,
            stderr=log,
            **spawn_options(),
        )
    job = store.job(job_id)
    if job["status"] == "queued":
        job["pid"] = child.pid
        job["process_identity"] = process_identity(child.pid)
        store.save_job(job)
    return child.pid


def cancel_job(store: Store, job_id: str):
    job = store.job(job_id)
    if job["status"] not in ("queued", "waiting", "running", "cancelling"):
        return
    if not alive(job):
        job.update(status="cancelled", message="已取消；已完成片段保留", finished_at=time.time())
    elif runner_owned(job, store.path):
        job.update(
            status="cancelling",
            message="正在取消，等待模型程序退出…",
            cancel_requested_at=job.get("cancel_requested_at", time.time()),
        )
        store.save_job(job)
        if sys.platform != "win32":
            try:
                signal_group(job["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        return
    else:
        raise ValueError("無法確認任務程序身分，已避免向不相關程序發送信號；請檢查任務紀錄。")
    store.save_job(job)


def recover(store: Store):
    for job in store.active_jobs():
        if job["status"] == "queued" and not job.get("pid") and time.time() - job["created_at"] < 5:
            continue
        if not alive(job):
            cancelled = job["status"] == "cancelling"
            for track_id, stages in job.get("stages", {}).items():
                for name, stage in stages.items():
                    if stage["status"] == "running":
                        stage.update(
                            status="cancelled" if cancelled else "interrupted", finished_at=time.time()
                        )
                        job["failed_stage"], job["failed_track"] = name, track_id
            job.update(
                status="cancelled" if cancelled else "interrupted",
                message="已取消；已完成片段保留" if cancelled else "前次程序已停止，可恢復",
                finished_at=time.time(),
            )
            store.save_job(job)
        elif job["status"] == "cancelling" and time.time() - job.get("cancel_requested_at", time.time()) > 10:
            if runner_owned(job, store.path):
                try:
                    signal_group(job["pid"], getattr(signal, "SIGKILL", 9))
                except ProcessLookupError:
                    pass


def result_fingerprint(store, track_id):
    from onsei.asr import ASR_CONFIG, ASSEMBLY_CONFIG, HAAS_CONFIG
    from onsei.translation_config import CONFIG, PROMPT_VERSION
    from onsei.context import DOCUMENT_CONFIG
    from onsei.models import REGISTRY

    track, cues = store.result(track_id)
    return {
        "implementation": [
            ASR_CONFIG,
            ASSEMBLY_CONFIG,
            HAAS_CONFIG,
            CONFIG,
            DOCUMENT_CONFIG,
            PROMPT_VERSION,
            json.loads(json.dumps(REGISTRY)),
        ],
        "version": track.active_version,
        "digest": cue_digest(cues),
        "revisions": [c.revision for c in cues],
        "settings": store.settings(),
        "translation": cues[0].provenance.get("translation_version") if cues else None,
    }


def resume_job(store, old):
    if old.get("options", {}).get("prototype"):
        raise ValueError("試用版任務保留為歷史紀錄，請用處理音軌或批量處理建立新版任務")
    if old["status"] in ("queued", "waiting", "running", "cancelling"):
        raise ValueError("Job already active")
    if old["action"] in ("transcribe", "run") and old["options"].get("asr", DEFAULT_ASR) not in ASR_MODELS:
        raise ValueError("此轉錄模型已停止支援，請選擇 Haas 建立新任務。")
    job = store.new_job(old["action"], old["options"])
    job["resume"] = {
        "job_id": old["id"],
        "stages": old.get("stages", {}),
        "snapshots": old.get("resume_snapshots", {}),
    }
    store.save_job(job)
    return job


if __name__ == "__main__":
    # POSIX can interrupt the runner so it closes its model subprocesses.
    def stop(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    run_job(Store(sys.argv[1]), sys.argv[2])
