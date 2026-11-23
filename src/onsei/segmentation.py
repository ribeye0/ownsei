"""Optional VAD input regions. Preserve raw detection and never fabricate timestamps."""

from __future__ import annotations
import json
import math
import subprocess
from onsei import backend
from onsei.models import model_dir, runtime_python, worker_env, SPEECH_FILES
from onsei.processes import stop_process
from onsei.util import atomic_json, digest
from onsei.vad_settings import normalize_settings


def validate_regions(regions, duration_ms):
    result, previous = [], 0
    for row in regions:
        a, b = row.get("start"), row.get("end")
        if any(
            isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in (a, b)
        ):
            raise ValueError("Invalid VAD region")
        start, end = max(0, round(a * 1000)), min(duration_ms, round(b * 1000))
        if start < previous or end <= start:
            raise ValueError("Overlapping, empty or out-of-order VAD regions")
        result.append(
            {
                "index": len(result),
                "start_ms": start,
                "end_ms": end,
                "owned_start_ms": start,
                "owned_end_ms": end,
            }
        )
        previous = end
    if not result:
        raise ValueError("VAD 沒有找到台詞；請降低門檻或選擇不使用 VAD，診斷檔保留")
    return result


def regions(audio, directory, duration_ms, settings, progress):
    engine = backend.current()["vad_engine"]
    config = normalize_settings(engine, settings)
    folder = directory / (
        "segmentation-" + digest({"engine": engine, "config": config, "duration_ms": duration_ms})[:12]
    )
    folder.mkdir(parents=True, exist_ok=True)
    output, request, log = folder / "vad.json", folder / "settings.json", folder / "vad.log"
    atomic_json(request, settings)
    if not output.exists():
        progress(
            {
                "stage": "vad",
                "message": f"{engine} VAD 切分台詞（CPU）",
                "log_source": str(log),
                "log_start": 0,
            }
        )
        if engine == "firered":
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
                str(request),
                "--limit-ms",
                str(duration_ms),
            ]
            env = worker_env("vad")
        else:
            command = [
                str(backend.binary("speech")),
                "vad",
                str(model_dir("silero-vad") / SPEECH_FILES["silero-vad"][0]),
                "--device",
                "CPU",
                "--format",
                "json",
                "--threshold",
                str(config["threshold"]),
                "--speech-pad-ms",
                str(config["pad_ms"]),
                "--min-speech-duration-ms",
                str(config["min_speech_ms"]),
                "--min-silence-duration-ms",
                str(config["min_silence_ms"]),
                "--max-speech-duration-s",
                str(config["max_region_ms"] / 1000),
                str(audio),
            ]
            env = backend.environment("speech")
        with log.open("w") as file:
            process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=file, text=True)
            try:
                lines = []
                for line in process.stdout:
                    lines.append(line)
                    if engine == "firered":
                        file.write(line)
                        file.flush()
                        progress(json.loads(line))
                if process.wait():
                    raise RuntimeError(f"{engine} VAD failed; see {log}\n{log.read_text()[-2000:]}")
                if engine == "silero":
                    atomic_json(output, json.loads("".join(lines)))
            finally:
                stop_process(process)
    data = json.loads(output.read_text())
    # Silero scans the PCM cache; cap it to this task's selected duration.
    values = [
        dict(r, end=min(r["end"], duration_ms / 1000))
        for r in data["regions"]
        if r["start"] < duration_ms / 1000
    ]
    return validate_regions(values, duration_ms), {
        "engine": engine,
        "settings": config,
        "report": str(output),
    }
