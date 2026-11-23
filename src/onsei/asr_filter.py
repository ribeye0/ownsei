"""Optional, auditable FireRed support check for complete Haas cue candidates."""

from __future__ import annotations
import json
import math
import subprocess
from onsei.vad_settings import normalize_settings, LIMITS
from onsei.util import atomic_json, digest
from onsei.models import model_dir, runtime_python, worker_env
from onsei.processes import stop_process


def normalize_filter(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or value.keys() - {"enabled", "settings", "min_overlap"}:
        raise ValueError("不支援的台詞篩選設定")
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必須為布林值")
    overlap = value.get("min_overlap", 0.25)
    if (
        isinstance(overlap, bool)
        or not isinstance(overlap, (int, float))
        or not math.isfinite(overlap)
        or not 0 <= overlap <= 1
    ):
        raise ValueError("台詞語音占比必須介於 0～1")
    settings = normalize_settings("firered", value.get("settings"))
    return {
        "enabled": enabled,
        "settings": {k: settings[k] for k in LIMITS},
        "min_overlap": float(overlap),
        "pipeline": 1,
    }


def filter_spans(spans, speech, min_overlap):
    """Preserve accepted text/timing and report unsupported candidates separately."""
    previous = 0
    for a, b in speech:
        if not 0 <= previous <= a < b:
            raise ValueError("Invalid FireRed speech intervals")
        previous = b
    kept, dropped = [], []
    for span in spans:
        start, end = span["start_ms"], span["end_ms"]
        coverage = sum(max(0, min(end, b) - max(start, a)) for a, b in speech)
        fraction = coverage / (end - start) if end > start else 0.0
        record = {**span, "vad_speech_fraction": round(fraction, 4)}
        if min_overlap == 0 or fraction >= min_overlap:
            kept.append(record)
        else:
            dropped.append(record)
    return kept, dropped


def detect_and_filter(audio, directory, duration_ms, spans, options, progress):
    folder = directory / ("speech-filter-" + digest(options)[:12])
    folder.mkdir(exist_ok=True)
    output = folder / "vad.json"
    log = folder / "vad.log"
    settings = folder / "settings.json"
    atomic_json(settings, options["settings"])
    if not output.exists():
        progress(
            {
                "stage": "vad",
                "message": "FireRedVAD 檢查台詞語音支持（CPU）",
                "log_source": str(log),
                "log_start": 0,
            }
        )
        command = [
            str(runtime_python("vad")),
            "-m",
            "onsei.vad_worker",
            str(audio),
            "--model",
            str(model_dir("firered-vad")),
            "--output",
            str(output),
            "--settings",
            str(settings),
            "--limit-ms",
            str(duration_ms),
        ]
        with log.open("w") as file:
            process = subprocess.Popen(
                command, env=worker_env("vad"), stdout=subprocess.PIPE, stderr=file, text=True
            )
            try:
                for line in process.stdout:
                    file.write(line)
                    file.flush()
                    progress(json.loads(line))
                if process.wait():
                    raise RuntimeError(f"FireRedVAD failed; see {log}")
            finally:
                stop_process(process)
    data = json.loads(output.read_text())
    kept, dropped = filter_spans(spans, data["spans_ms"], options["min_overlap"])
    report = folder / "filter-report.json"
    report_data = {"settings": options, "kept": len(kept), "dropped": dropped, "candidates": len(spans)}
    atomic_json(report, report_data)
    report_log = folder / "filter-report.log"
    report_log.write_text(json.dumps(report_data, ensure_ascii=False, indent=2) + "\n")
    progress(
        {
            "stage": "vad",
            "log_source": str(report_log),
            "log_start": 0,
            "message": f"台詞篩選：保留 {len(kept)}/{len(spans)} 句，排除 {len(dropped)} 句；詳見 {report}",
        }
    )
    return kept, {
        "active": True,
        "mode": "post_asr_support",
        "settings": options,
        "dropped": len(dropped),
        "report": str(report),
    }
