from __future__ import annotations

import json
import re
import subprocess
import os
import sys
from onsei import backend
import queue
import threading
import time
import unicodedata
from pathlib import Path

from onsei.audio import chunks, pcm, pcm_use_lock
from onsei.domain import Cue, subtitle_ja
from onsei.models import (
    ASR_MODELS,
    REGISTRY,
    model_dir,
    SPEECH_FILES,
)
from onsei.storage import Store
from onsei.util import atomic_json, digest
from onsei.processes import stop_process

ASR_CONFIG = {"window_ms": 24000, "overlap_ms": 1000, "beam": 3, "language": "ja", "pipeline": 1}
ASSEMBLY_CONFIG = {"pipeline": 1, "empty_rendered_cues": "omit_preserve_worker_audit"}
HAAS_CONFIG = {"pipeline": 3, "engine": "speech.cpp", "version": "0.11.2", "timestamps": "native_tdt"}


def invoke(model: Path, audio: Path, items: list[dict], directory: Path, progress):
    kind = "speech"
    pending = []
    for item in items:
        checkpoint = Path(item["output"])
        if checkpoint.exists():
            try:
                saved = json.loads(checkpoint.read_text())
                data = saved["data"]
                if not isinstance(data, dict):
                    raise ValueError("Invalid worker checkpoint")
            except (ValueError, KeyError, TypeError):
                checkpoint.replace(checkpoint.with_suffix(f".{time.time_ns()}.invalid.json"))
                progress({"message": f"檢查點損壞，保留原檔並重算：{checkpoint.name}", "stage": kind})
        if not checkpoint.exists():
            pending.append(item)
    if not pending:
        return
    request = directory / f"{kind}-request.json"
    payload = {"kind": kind, "model": str(model), "audio": str(audio), "items": pending}
    payload.update(device=backend.device("speech"), filename=SPEECH_FILES[model.name][0])
    atomic_json(request, payload)
    log = directory / f"{kind}.log"
    progress(
        {
            "log_source": str(log),
            "log_start": log.stat().st_size if log.exists() else 0,
            "message": f"載入 {kind} 模型",
            "stage": kind,
        }
    )
    with log.open("a", encoding="utf-8") as errors:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "onsei.speech_worker",
                str(request),
            ],
            env=backend.environment("speech"),
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
        )
        done = len(items) - len(pending)
        worker_error = None
        try:
            lines = queue.Queue()

            def read_output():
                try:
                    for line in process.stdout:
                        lines.put(line)
                finally:
                    lines.put(None)

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            idle_timeout = float(os.environ.get("ONSEI_WORKER_IDLE_TIMEOUT", "900"))
            last_output = time.monotonic()
            while True:
                try:
                    line = lines.get(timeout=min(1, idle_timeout))
                except queue.Empty:
                    if time.monotonic() - last_output > idle_timeout:
                        raise RuntimeError(
                            f"{kind} 模型超過 {idle_timeout:g} 秒沒有進展；已停止程序，檢查點保留。See {log}"
                        )
                    continue
                if line is None:
                    break
                last_output = time.monotonic()
                try:
                    event = json.loads(line)
                    if event.get("error"):
                        worker_error = str(event["error"])
                    if "completed" in event:
                        done += 1
                        event["message"] = f"Haas / speech.cpp 日文轉錄：{done}/{len(items)} 片段"
                        event["stage"] = kind
                        event["fraction"] = done / len(items)
                    progress(event)
                except json.JSONDecodeError:
                    errors.write(line)
                    errors.flush()
            try:
                returncode = process.wait(timeout=idle_timeout)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"{kind} 模型輸出已關閉，但超過 {idle_timeout:g} 秒仍未退出；已停止程序。See {log}"
                ) from exc
            if returncode:
                errors.flush()
                tail = log.read_text()[-3000:]
                detail = f"：{worker_error}" if worker_error else ""
                raise RuntimeError(f"{kind} worker failed (exit {returncode}){detail}. See {log}\n{tail}")
        finally:
            stop_process(process)


def normalize(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", text).casefold()
        if not c.isspace() and not unicodedata.category(c).startswith("P")
    )


def merge_spans(spans: list[dict]) -> list[dict]:
    """Remove overlap copies only when the same text occupies the same audio interval."""
    result = []
    for s in sorted(spans, key=lambda v: (v["start_ms"], v["end_ms"])):
        if s["end_ms"] <= s["start_ms"] or not s["text"].strip():
            continue
        duplicate = any(
            normalize(s["text"]) == normalize(p["text"])
            and min(s["end_ms"], p["end_ms"]) - max(s["start_ms"], p["start_ms"])
            > 0.45 * min(s["end_ms"] - s["start_ms"], p["end_ms"] - p["start_ms"])
            for p in result[-6:]
        )
        if not duplicate:
            result.append(s)
    return result


def segment_spans(segment: dict, part: dict) -> list[dict]:
    words = segment.get("words") or []
    owned = [
        w
        for w in words
        if part["owned_start_ms"] <= part["start_ms"] + (w["start"] + w["end"]) * 500 < part["owned_end_ms"]
    ]
    if not words:
        return [
            {
                "text": segment["text"],
                "start_ms": part["start_ms"] + round(segment["start"] * 1000),
                "end_ms": part["start_ms"] + round(segment["end"] * 1000),
                "confidence": segment.get("avg_logprob"),
            }
        ]
    spans, buffer = [], []

    def flush(count=None):
        if not buffer:
            return
        take = buffer[:count] if count is not None else buffer[:]
        confidence = [w.get("confidence", segment.get("avg_logprob")) for w in take]
        spans.append(
            {
                "text": "".join(w["text"] for w in take).strip(),
                "start_ms": part["start_ms"] + round(take[0]["start"] * 1000),
                "end_ms": part["start_ms"] + round(take[-1]["end"] * 1000),
                "confidence": sum(confidence) / len(take) if all(v is not None for v in confidence) else None,
                "chunk": take[0].get("chunk"),
                "owned": True,
            }
        )
        del buffer[: len(take)]

    for word in owned:
        if buffer and word["start"] - buffer[-1]["end"] > 0.7:
            flush()
        buffer.append(word)
        if re.search(r"[。！？!?]$", word["text"].strip()):
            flush()
        elif sum(len(w["text"]) for w in buffer) > 42 or (
            word["end"] - buffer[0]["start"] > 7 and sum(len(w["text"]) for w in buffer) >= 8
        ):
            # Japanese timestamp tokens can be single characters, not linguistic words.
            # Prefer a punctuation boundary; retain a long cue when no boundary exists.
            boundaries = [
                i + 1
                for i, w in enumerate(buffer)
                if re.search(r"[、，,；;…]$", w["text"].strip())
                and sum(len(x["text"]) for x in buffer[: i + 1]) >= 4
            ]
            if boundaries:
                flush(boundaries[-1])
    flush()
    return spans


def native_track_spans(parts: list[dict]) -> list[dict]:
    """Segment owned tokens across ASR windows so a window edge does not split a cue."""
    words, fallback = [], []
    for part in parts:
        segment = json.loads(Path(part["output"]).read_text())["data"]
        if not segment.get("text", "").strip() and not segment.get("words"):
            continue
        if not segment.get("words"):
            fallback.extend({**s, "chunk": part} for s in segment_spans(segment, part))
        for w in segment.get("words") or []:
            midpoint = part["start_ms"] + (w["start"] + w["end"]) * 500
            if part["owned_start_ms"] <= midpoint < part["owned_end_ms"]:
                words.append(
                    {
                        **w,
                        "start": w["start"] + part["start_ms"] / 1000,
                        "end": w["end"] + part["start_ms"] / 1000,
                        "chunk": part,
                        "confidence": segment.get("avg_logprob"),
                    }
                )
    words.sort(key=lambda w: (w["start"], w["end"]))
    if not words:
        return fallback
    segment = {"words": words, "avg_logprob": 0}
    part = {"start_ms": 0, "owned_start_ms": 0, "owned_end_ms": parts[-1]["owned_end_ms"] + 1000}
    return fallback + segment_spans(segment, part)


def transcribe(
    store: Store,
    track_id: str,
    model: str,
    limit_ms: int | None,
    progress,
    *,
    generation=None,
    asr_filter=None,
) -> list[Cue]:
    if model not in ASR_MODELS:
        raise ValueError("Unsupported ASR")
    from onsei.asr_filter import normalize_filter

    filter_options = normalize_filter(asr_filter)
    track = store.track(track_id)
    signature = {
        "audio": track.sha256,
        "model": REGISTRY[model][:2],
        "config": ASR_CONFIG,
        "limit_ms": limit_ms,
    }
    signature["haas"] = HAAS_CONFIG
    signature["weights"] = SPEECH_FILES[model]
    signature["execution"] = {
        "device": backend.device("speech"),
        "vad_engine": backend.current()["vad_engine"],
        "vad_settings": filter_options["settings"],
    }
    if backend.current()["vad_engine"] == "firered":
        signature["execution"]["vad_revision"] = REGISTRY["firered-vad"][1]
    generation = generation or store.asr_nonce(track_id, model)
    if generation:
        signature["generation"] = generation
    cache_version = digest(signature)[:16]
    version = digest(
        {
            **signature,
            "assembly": ASSEMBLY_CONFIG,
            **({"asr_filter": filter_options} if filter_options["enabled"] else {}),
        }
    )[:16]
    if existing := store.cues(track_id, version):
        from onsei.source import validate_source

        validate_source(store, track)
        store.activate_transcript(track.id, version)
        return existing
    with pcm_use_lock(store, track):
        audio = pcm(store, track)
        directory = store.path / "cache" / track_id / cache_version
        directory.mkdir(parents=True, exist_ok=True)
        parts = chunks(audio, limit_ms)
        processed_duration_ms = parts[-1]["owned_end_ms"]
        segmentation = None
        if backend.current()["vad_engine"] != "none":
            from onsei.segmentation import regions

            settings = dict(filter_options["settings"])
            if backend.current()["vad_engine"] == "silero":
                settings["max_gain_db"] = 0
            parts, segmentation = regions(audio, directory, processed_duration_ms, settings, progress)
        for part in parts:
            part["output"] = str(directory / f"asr-{part['index']:05d}.json")
        invoke(model_dir(model), audio, parts, directory, progress)
        spans = merge_spans(native_track_spans(parts))
        vad_info = None
        if filter_options["enabled"]:
            from onsei.asr_filter import detect_and_filter

            spans, vad_info = detect_and_filter(
                audio, directory, processed_duration_ms, spans, filter_options, progress
            )
    cues = []
    for span in spans:
        if not subtitle_ja(span["text"]).strip():
            continue
        start, end = max(0, span["start_ms"]), min(span["end_ms"], track.duration_ms)
        if end <= start:
            continue
        flags = list(span.get("review_flags", []))
        if end - start > 12000 or len(span["text"]) > 65:
            flags.append("long_cue")
        if span.get("confidence") is not None and span["confidence"] < -0.8:
            flags.append("low_asr_confidence")
        index = len(cues)
        cues.append(
            Cue(
                id=f"{track_id}-{version}-{index:05d}",
                track_id=track_id,
                version=version,
                ordinal=index,
                start_ms=start,
                end_ms=end,
                ja_raw=span["text"],
                review_flags=flags,
                timing_status="asr",
                provenance={
                    "model": model,
                    "revision": REGISTRY[model][1],
                    "config": ASR_CONFIG,
                    "assembly_config": ASSEMBLY_CONFIG,
                    "haas": HAAS_CONFIG,
                    "chunk": span["chunk"],
                    "execution": backend.current(),
                    **({"segmentation": segmentation} if segmentation else {}),
                    "confidence": span.get("confidence"),
                    **(
                        {"vad": vad_info, "vad_speech_fraction": span.get("vad_speech_fraction")}
                        if vad_info
                        else {}
                    ),
                    "processed_duration_ms": processed_duration_ms,
                    "limit_ms": limit_ms,
                },
            )
        )
    if not cues:
        raise RuntimeError(
            "台詞篩選後沒有可用台詞；請降低門檻或語音占比。原始 ASR 與篩選紀錄保留"
            if vad_info
            else "No speech cues produced; raw results retained for review"
        )
    store.replace_cues(track, version, cues)
    return cues
