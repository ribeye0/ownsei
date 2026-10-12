from __future__ import annotations

import json
import math
import re
import subprocess
import os
import queue
import threading
import time
import unicodedata
from pathlib import Path

from onsei.audio import chunks, pcm
from onsei.domain import Cue
from onsei.models import (
    ASR_MODELS,
    WHISPER_MODELS,
    WHISPER_CONVERSIONS,
    WHISPER_CONVERSION_CONFIG,
    REGISTRY,
    model_dir,
    runtime_python,
    worker_env,
)
from onsei.storage import Store
from onsei.util import atomic_json, digest
from onsei.processes import stop_process

ASR_CONFIG = {"window_ms": 24000, "overlap_ms": 1000, "beam": 3, "language": "ja", "pipeline": 5}
ALIGNMENT_CONFIG = {
    "pipeline": 4,
    "timestamp_resolution_ms": 80,
    "zero_duration": "merge_adjacent_and_flag",
    "ownership": "character_midpoint",
}
REALIGNMENT_CONFIG = {
    "pipeline": 4,
    "timestamp_resolution_ms": 80,
    "retry_pad_ms": 500,
    "native_max_shift_ms": 750,
    "fallback": "unchanged_native_whisper",
    "window": "native_speech_bounds",
}


def native_timing_guard(start: int, end: int, native: Cue | None) -> str | None:
    if native is None:
        return None
    if (
        max(abs(start - native.start_ms), abs(end - native.end_ms))
        > REALIGNMENT_CONFIG["native_max_shift_ms"]
    ):
        return "Native timing guard: boundary shifted more than 750 ms"
    if end <= native.start_ms or start >= native.end_ms:
        return "Native timing guard: no overlap with native speech"
    if native.end_ms - native.start_ms > 1000 and end - start < (native.end_ms - native.start_ms) / 4:
        return "Native timing guard: speech interval collapsed"
    return None


def invoke(kind: str, model: Path, audio: Path, items: list[dict], directory: Path, progress):
    pending = []
    for item in items:
        checkpoint = Path(item["output"])
        if checkpoint.exists():
            try:
                saved = json.loads(checkpoint.read_text())
                data = saved["data"]
                if not isinstance(data, (list, dict)):
                    raise ValueError("Invalid worker checkpoint")
            except (ValueError, KeyError, TypeError):
                checkpoint.replace(checkpoint.with_suffix(f".{time.time_ns()}.invalid.json"))
                progress({"message": f"檢查點損壞，保留原檔並重算：{checkpoint.name}", "stage": kind})
        if not checkpoint.exists():
            pending.append(item)
    if not pending:
        return
    request = directory / f"{kind}-request.json"
    atomic_json(request, {"kind": kind, "model": str(model), "audio": str(audio), "items": pending})
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
            [str(runtime_python(kind)), "-m", "onsei.worker", str(request)],
            env=worker_env(kind),
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
        )
        done = len(items) - len(pending)
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
                    if "completed" in event:
                        done += 1
                        stage = {
                            "whisper": "Whisper 日文轉錄",
                            "cohere": "Cohere 日文轉錄",
                            "aligner": "日文時間對齊",
                        }[kind]
                        event["message"] = f"{stage}：{done}/{len(items)} 片段"
                        event["stage"] = kind
                        event["fraction"] = done / len(items)
                    progress(event)
                except json.JSONDecodeError:
                    errors.write(line)
            try:
                returncode = process.wait(timeout=idle_timeout)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"{kind} 模型輸出已關閉，但超過 {idle_timeout:g} 秒仍未退出；已停止程序。See {log}"
                ) from exc
            if returncode:
                tail = log.read_text()[-3000:]
                raise RuntimeError(f"{kind} worker failed. See {log}\n{tail}")
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


def whisper_spans(segment: dict, part: dict) -> list[dict]:
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
                "confidence": segment["avg_logprob"],
            }
        ]
    spans, buffer = [], []

    def flush(count=None):
        if not buffer:
            return
        take = buffer[:count] if count is not None else buffer[:]
        spans.append(
            {
                "text": "".join(w["text"] for w in take).strip(),
                "start_ms": part["start_ms"] + round(take[0]["start"] * 1000),
                "end_ms": part["start_ms"] + round(take[-1]["end"] * 1000),
                "confidence": sum(w.get("confidence", segment["avg_logprob"]) for w in take) / len(take),
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


def whisper_track_spans(parts: list[dict]) -> list[dict]:
    """Segment owned tokens across ASR windows so a window edge does not split a cue."""
    words, fallback = [], []
    for part in parts:
        for segment in json.loads(Path(part["output"]).read_text())["data"]:
            if not segment.get("words"):
                fallback.extend({**s, "chunk": part} for s in whisper_spans(segment, part))
            for w in segment.get("words") or []:
                midpoint = part["start_ms"] + (w["start"] + w["end"]) * 500
                if part["owned_start_ms"] <= midpoint < part["owned_end_ms"]:
                    words.append(
                        {
                            **w,
                            "start": w["start"] + part["start_ms"] / 1000,
                            "end": w["end"] + part["start_ms"] / 1000,
                            "chunk": part,
                            "confidence": segment["avg_logprob"],
                        }
                    )
    words.sort(key=lambda w: (w["start"], w["end"]))
    if not words:
        return fallback
    segment = {"words": words, "avg_logprob": 0}
    part = {"start_ms": 0, "owned_start_ms": 0, "owned_end_ms": parts[-1]["owned_end_ms"] + 1000}
    return fallback + whisper_spans(segment, part)


def alignment_times(text: str, words: list[dict], duration_ms: int | None = None) -> list[tuple]:
    """Verify complete text coverage and reject corrupt times, but allow model point tokens."""
    if not normalize(text):
        return []
    if not words:
        raise ValueError("Aligner returned no tokens")
    times = []
    previous = 0.0
    for w in words:
        start, end = w["start"], w["end"]
        if (
            not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < previous
            or end < start
            or (duration_ms is not None and round(end * 1000) > duration_ms + 80)
        ):
            raise ValueError("Aligner returned invalid token times")
        # Qwen timestamps have 80 ms resolution. Clip a single-bin edge excess,
        # without accepting negative, reversed, or substantially out-of-bounds times.
        if duration_ms is not None:
            start, end = min(start, duration_ms / 1000), min(end, duration_ms / 1000)
        previous = end
        times.extend([(start, end)] * len(normalize(w["text"])))
    if len(times) != len(normalize(text)) or normalize("".join(w["text"] for w in words)) != normalize(text):
        raise ValueError("Aligned tokens do not cover transcript characters")
    return times


def aligned_sentences(
    text: str,
    words: list[dict],
    offset: int,
    duration_ms: int | None = None,
    owned_range: tuple[int, int] | None = None,
) -> list[dict]:
    """Keep original punctuation; locate its characters in aligner token order."""
    character_times = alignment_times(text, words, duration_ms)
    pieces = re.findall(r".+?(?:[。！？!?]+|$)", text, flags=re.S)
    cursor = 0
    spans = []
    prefix = ""
    for p in pieces:
        n = len(normalize(p))
        if not n:
            continue
        times = character_times[cursor : cursor + n]
        cursor += n
        if owned_range is not None:
            # A sentence midpoint cannot determine ownership of words at an
            # overlapping window edge. Trim using aligned character times first.
            keep = [owned_range[0] <= offset + (a + b) * 500 < owned_range[1] for a, b in times]
            chars, retained, index = [], [], 0
            previous_kept = False
            for char in p:
                count = len(normalize(char))
                if count:
                    previous_kept = all(keep[index : index + count])
                    if previous_kept:
                        chars.append(char)
                        retained.extend(times[index : index + count])
                    index += count
                elif previous_kept or (index == 0 and keep[0]):
                    chars.append(char)
            p, times = "".join(chars), retained
            if not times:
                continue
        # Point tokens are legitimate aligner output, especially short sounds at window edges.
        # A point-only sentence has no measured duration: retain it with an adjacent timed
        # sentence, rather than deleting text or inventing a subtitle interval.
        supported = [(a, b) for a, b in times if round(b * 1000) > round(a * 1000)]
        flags = (
            ["alignment_zero_duration"] if any(round(a * 1000) == round(b * 1000) for a, b in times) else []
        )
        if not supported:
            if spans:
                spans[-1]["text"] += p.strip()
                spans[-1]["review_flags"] = ["alignment_zero_duration", "alignment_merged"]
            else:
                prefix += p.strip()
            continue
        start, end = supported[0][0], supported[-1][1]
        if prefix:
            flags = ["alignment_zero_duration", "alignment_merged"]
        spans.append(
            {
                "text": prefix + p.strip(),
                "start_ms": offset + round(start * 1000),
                "end_ms": offset + round(end * 1000),
                "review_flags": flags,
            }
        )
        prefix = ""
    if prefix:
        raise ValueError("Aligner returned no positive-duration interval; raw transcript retained")
    return spans


def transcribe(store: Store, track_id: str, model: str, limit_ms: int | None, progress, *, generation=None) -> list[Cue]:
    if model not in ASR_MODELS:
        raise ValueError("Unsupported ASR")
    track = store.track(track_id)
    signature = {
        "audio": track.sha256,
        "model": REGISTRY[model][:2],
        "config": ASR_CONFIG,
        "limit_ms": limit_ms,
    }
    if model in WHISPER_CONVERSIONS:
        signature["conversion"] = WHISPER_CONVERSION_CONFIG
    generation = generation or store.asr_nonce(track_id, model)
    if generation:
        signature["generation"] = generation
    cache_version = digest(signature)[:16]
    # Raw recognition/alignment checkpoints are unchanged; only Cohere cue assembly changes.
    version = (
        digest({**signature, "alignment": ALIGNMENT_CONFIG})[:16]
        if model == "cohere-asr-ja"
        else cache_version
    )
    existing = store.cues(track_id, version)
    if existing:
        from onsei.source import validate_source

        validate_source(store, track)
        store.activate_transcript(track.id, version)
        return existing
    audio = pcm(store, track)
    directory = store.path / "cache" / track_id / cache_version
    directory.mkdir(parents=True, exist_ok=True)
    parts = chunks(audio, limit_ms)
    for part in parts:
        part["output"] = str(directory / f"asr-{part['index']:05d}.json")
    kind = REGISTRY[model][2]
    invoke(kind, model_dir(model), audio, parts, directory, progress)
    if kind == "cohere":
        raw_text = "\n".join(json.loads(Path(part["output"]).read_text())["data"]["text"] for part in parts)
        raw_path = store.path / "exports" / f"{track.id}-{version}.ja-unaligned.txt"
        raw_path.parent.mkdir(exist_ok=True)
        raw_path.write_text(raw_text, encoding="utf-8")
        align = []
        for part in parts:
            text = json.loads(Path(part["output"]).read_text())["data"]["text"]
            if text:
                align.append(
                    {**part, "text": text, "output": str(directory / f"align-{part['index']:05d}.json")}
                )
        invoke("aligner", model_dir("aligner"), audio, align, directory, progress)
    spans = whisper_track_spans(parts) if kind == "whisper" else []
    for part in parts:
        if kind == "whisper":
            continue
        raw = json.loads(Path(part["output"]).read_text())["data"]
        if not raw["text"]:
            continue
        words = json.loads((directory / f"align-{part['index']:05d}.json").read_text())["data"]
        try:
            local = aligned_sentences(
                raw["text"],
                words,
                part["start_ms"],
                part["end_ms"] - part["start_ms"],
                (part["owned_start_ms"], part["owned_end_ms"]),
            )
        except ValueError as e:
            raise ValueError(f"Alignment chunk {part['index'] + 1}: {e}") from e
        for s in local:
            s["chunk"] = part
            spans.append(s)
    cues = []
    for n, s in enumerate(merge_spans(spans)):
        s["end_ms"] = min(s["end_ms"], track.duration_ms)
        flags = list(s.get("review_flags", []))
        if s["end_ms"] - s["start_ms"] > 12000 or len(s["text"]) > 65:
            flags.append("long_cue")
        if s.get("confidence", 0) < -0.8:
            flags.append("low_asr_confidence")
        c = Cue(
            id=f"{track_id}-{version}-{n:05d}",
            track_id=track_id,
            version=version,
            ordinal=n,
            start_ms=s["start_ms"],
            end_ms=s["end_ms"],
            ja_raw=s["text"],
            review_flags=flags,
            timing_status="asr" if kind == "whisper" else "aligned",
            provenance={
                "model": model,
                "revision": REGISTRY[model][1],
                "config": ASR_CONFIG,
                **({"conversion": WHISPER_CONVERSION_CONFIG} if model in WHISPER_CONVERSIONS else {}),
                **({"alignment_config": ALIGNMENT_CONFIG} if kind == "cohere" else {}),
                "chunk": s["chunk"],
                "confidence": s.get("confidence"),
                "processed_duration_ms": parts[-1]["owned_end_ms"],
                "limit_ms": limit_ms,
            },
        )
        cues.append(c)
    if not cues:
        raise RuntimeError("No speech cues produced; raw results retained for review")
    store.replace_cues(track, version, cues)
    return cues


def alignment_copy(store: Store, track_id: str) -> bool:
    """Preserve native Whisper timing so disabling extra alignment can select it again."""
    track = store.track(track_id)
    source = store.cues(track_id)
    if not source:
        raise ValueError("No transcript to align")
    version = digest(
        {
            "parent": source[0].version,
            "aligner": REGISTRY["aligner"][1],
            "config": REALIGNMENT_CONFIG,
            "sources": [(c.ja, c.start_ms, c.end_ms) for c in source],
        }
    )[:16]
    existing = store.cues(track_id, version)
    if existing:
        store.activate_transcript(track.id, version)
        return any(
            c.timing_status in ("stale", "alignment_failed")
            or (c.timing_status == "asr" and "alignment_native_fallback" not in c.review_flags)
            for c in existing
        )
    copies = []
    for c in source:
        c = c.model_copy(deep=True)
        parent = c.version
        c.id, c.version = f"{track_id}-{version}-{c.ordinal:05d}", version
        c.zh_auto = c.zh_override = None
        c.translation_status, c.revision = "pending", 0
        c.provenance.pop("translation", None)
        c.provenance.pop("translation_version", None)
        c.provenance.update(
            extra_alignment=True, parent_version=parent, extra_alignment_config=REALIGNMENT_CONFIG
        )
        copies.append(c)
    store.replace_cues(track, version, copies)
    return True


def realign(store: Store, track_id: str, selected: list[str] | None, progress):
    track = store.track(track_id)
    cues = store.cues(track_id)
    wanted = set(selected or [c.id for c in cues if c.timing_status in ("stale", "alignment_failed")])
    if not wanted:
        return
    if wanted - {c.id for c in cues}:
        raise ValueError("Selected cues are not in the active transcript")
    if any("chunk" not in c.provenance for c in cues if c.id in wanted):
        raise ValueError("Selected cues have no audio chunk provenance for alignment")
    groups = {c.provenance["chunk"]["index"] for c in cues if c.id in wanted and "chunk" in c.provenance}
    directory = store.path / "cache" / track_id / "realign"
    directory.mkdir(parents=True, exist_ok=True)
    native_by_id = {}
    parents = {}
    for c in cues:
        parent = c.provenance.get("parent_version")
        if c.provenance.get("extra_alignment") and c.provenance.get("model") in WHISPER_MODELS and parent:
            if parent not in parents:
                parents[parent] = {p.ordinal: p for p in store.cues(track_id, parent)}
            native = parents[parent].get(c.ordinal)
            if native and native.ja == c.ja and native.timing_status == "asr":
                native_by_id[c.id] = native
    snapshots = []
    for group in groups:
        members = [c for c in cues if c.provenance.get("chunk", {}).get("index") == group]
        part = dict(members[0].provenance["chunk"])
        part["start_ms"] = max(0, min(part["start_ms"], min(c.start_ms for c in members) - 500))
        part["end_ms"] = min(track.duration_ms, max(part["end_ms"], max(c.end_ms for c in members) + 500))
        if all(c.id in native_by_id for c in members):
            # Chunk edges may contain the previous cue's tail without its text.
            # Do not let that unrelated speech attract the next short utterance.
            part["start_ms"] = max(0, min(native_by_id[c.id].start_ms for c in members) - 500)
            part["end_ms"] = min(track.duration_ms, max(native_by_id[c.id].end_ms for c in members) + 500)
            first, last = members[0], members[-1]
            adjacent = parents[first.provenance["parent_version"]]
            before, after = adjacent.get(first.ordinal - 1), adjacent.get(last.ordinal + 1)
            start, end = native_by_id[first.id].start_ms, native_by_id[last.id].end_ms
            if before and before.end_ms <= start:
                part["start_ms"] = max(part["start_ms"], (before.end_ms + start) // 2)
            if after and after.start_ms >= end:
                part["end_ms"] = min(part["end_ms"], (end + after.start_ms) // 2)
        text = "".join(c.ja for c in members)
        key = digest(
            {"text": text, "part": part, "aligner": REGISTRY["aligner"][1], "config": REALIGNMENT_CONFIG}
        )
        item = {**part, "text": text, "output": str(directory / f"{key}.json")}
        snapshots.append((item, members))
    invoke("aligner", model_dir("aligner"), pcm(store, track), [s[0] for s in snapshots], directory, progress)
    retry = []
    for item, members in snapshots:
        words = json.loads(Path(item["output"]).read_text())["data"]
        # Map each original cue using character coverage; retain cue IDs and human text.
        try:
            times = alignment_times("".join(c.ja for c in members), words, item["end_ms"] - item["start_ms"])
            good = True
        except ValueError as e:
            times, good, reason = [], False, str(e)
        index = 0
        for c in members:
            n = len(normalize(c.ja))
            if good and n:
                start = item["start_ms"] + round(times[index][0] * 1000)
                end = min(track.duration_ms, item["start_ms"] + round(times[index + n - 1][1] * 1000))
                guard_reason = native_timing_guard(start, end, native_by_id.get(c.id))
                if end > start and not guard_reason:
                    c.start_ms, c.end_ms, c.timing_status = start, end, "aligned"
                    c.provenance["alignment_diagnostic"] = {"method": "group", "cache_file": item["output"]}
                else:
                    c.timing_status = "alignment_failed"
                    reason = guard_reason or "No positive-duration interval for cue"
                    c.provenance["alignment_diagnostic"] = {"group_candidate_ms": [start, end]}
            else:
                c.timing_status = "alignment_failed"
                if good:
                    reason = "No normalized transcript characters for cue"
            index += n
            c.provenance["alignment_revision"] = REGISTRY["aligner"][1]
            c.provenance["realignment_config"] = REALIGNMENT_CONFIG
            if c.timing_status == "alignment_failed":
                # A short sound may collapse into the following word in a long text.
                # Retry the unchanged cue in a narrow audio window; never invent duration.
                anchor = native_by_id.get(c.id, c)
                local = {
                    "start_ms": max(0, anchor.start_ms - REALIGNMENT_CONFIG["retry_pad_ms"]),
                    "end_ms": min(track.duration_ms, anchor.end_ms + REALIGNMENT_CONFIG["retry_pad_ms"]),
                    "text": c.ja,
                }
                if c.id in native_by_id:
                    adjacent = parents[c.provenance["parent_version"]]
                    before, after = adjacent.get(c.ordinal - 1), adjacent.get(c.ordinal + 1)
                    if before and before.end_ms <= anchor.start_ms:
                        local["start_ms"] = max(local["start_ms"], (before.end_ms + anchor.start_ms) // 2)
                    if after and after.start_ms >= anchor.end_ms:
                        local["end_ms"] = min(local["end_ms"], (anchor.end_ms + after.start_ms) // 2)
                key = digest({**local, "aligner": REGISTRY["aligner"][1], "config": REALIGNMENT_CONFIG})
                local["output"] = str(directory / f"retry-{key}.json")
                retry.append((local, c, reason))
                progress(
                    {
                        "message": f"句段 #{c.ordinal + 1} 整段對齊失敗：{reason}；改用單句音訊重試",
                        "stage": "aligner",
                        "cue_id": c.id,
                        "alignment_cache": item["output"],
                    }
                )
            else:
                c.review_flags = [f for f in c.review_flags if f != "alignment_native_fallback"]
                store.save_generated(c, c.revision)
    if retry:
        invoke("aligner", model_dir("aligner"), pcm(store, track), [s[0] for s in retry], directory, progress)
    for item, c, group_reason in retry:
        try:
            words = json.loads(Path(item["output"]).read_text())["data"]
            times = alignment_times(c.ja, words, item["end_ms"] - item["start_ms"])
            if not times or round(times[-1][1] * 1000) <= round(times[0][0] * 1000):
                raise ValueError("No positive-duration interval for cue")
            c.start_ms = item["start_ms"] + round(times[0][0] * 1000)
            c.end_ms = item["start_ms"] + round(times[-1][1] * 1000)
            if guard_reason := native_timing_guard(c.start_ms, c.end_ms, native_by_id.get(c.id)):
                raise ValueError(guard_reason)
            c.timing_status = "aligned"
            c.review_flags = [f for f in c.review_flags if f != "alignment_native_fallback"]
            diagnostic = {"method": "local_retry", "group_reason": group_reason}
            progress(
                {
                    "message": f"句段 #{c.ordinal + 1} 單句對齊成功：{c.start_ms}–{c.end_ms} ms",
                    "stage": "aligner",
                    "cue_id": c.id,
                }
            )
        except ValueError as e:
            diagnostic = {"method": "failed", "group_reason": group_reason, "reason": str(e)}
            native = native_by_id.get(c.id)
            if (
                c.provenance.get("extra_alignment")
                and c.provenance.get("model") in WHISPER_MODELS
                and native
                and native.ja == c.ja
                and native.timing_status == "asr"
                and native.end_ms <= track.duration_ms
            ):
                c.start_ms, c.end_ms, c.timing_status = native.start_ms, native.end_ms, "asr"
                if "alignment_native_fallback" not in c.review_flags:
                    c.review_flags.append("alignment_native_fallback")
                diagnostic["method"] = "native_fallback"
                progress(
                    {
                        "message": f"句段 #{c.ordinal + 1} 對齊不可靠（{e}）；保留 Whisper 原生時間，請回聽",
                        "stage": "aligner",
                        "cue_id": c.id,
                    }
                )
            else:
                c.timing_status = "alignment_failed"
                progress(
                    {
                        "message": f"句段 #{c.ordinal + 1} 單句對齊失敗：{e}",
                        "stage": "aligner",
                        "cue_id": c.id,
                    }
                )
        native = native_by_id.get(c.id)
        c.provenance["alignment_diagnostic"] = {
            **(
                {"group_candidate_ms": c.provenance["alignment_diagnostic"]["group_candidate_ms"]}
                if "group_candidate_ms" in c.provenance.get("alignment_diagnostic", {})
                else {}
            ),
            **diagnostic,
            "cache_file": item["output"],
            **({"native_interval_ms": [native.start_ms, native.end_ms]} if native else {}),
        }
        store.save_generated(c, c.revision)
    failed = [
        c.id
        for c in store.cues(track_id)
        if c.provenance.get("chunk", {}).get("index") in groups and c.timing_status == "alignment_failed"
    ]
    if failed:
        raise RuntimeError(f"Alignment failed for {len(failed)} cues; original times and text retained")
