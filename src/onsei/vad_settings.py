"""Validated, per-request VAD settings; no inference dependencies."""

from __future__ import annotations
import math

LIMITS = {
    "threshold": (0.05, 0.95, False),
    "min_speech_ms": (50, 2000, True),
    "min_silence_ms": (100, 3000, True),
    "pad_ms": (0, 1500, True),
    "max_region_ms": (5000, 30000, True),
    "max_gain_db": (0, 24, False),
}
DEFAULTS = {
    "threshold": 0.4,
    "min_speech_ms": 200,
    "min_silence_ms": 900,
    "pad_ms": 400,
    "max_region_ms": 24000,
    "max_gain_db": 24,
}
INTERNAL = {"block_ms": 60000, "context_ms": 2000, "gain_peak": 0.25}


def normalize_settings(engine, values=None):
    if engine not in ("firered", "silero"):
        raise ValueError("不支援的 VAD 模型")
    values = {} if values is None else values
    if not isinstance(values, dict) or values.keys() - LIMITS.keys():
        raise ValueError("不支援的 VAD 參數")
    result = {**DEFAULTS, **({"max_gain_db": 0} if engine == "silero" else {}), **values}
    for name, (lower, upper, integer) in LIMITS.items():
        value = result[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not lower <= value <= upper
            or integer
            and int(value) != value
        ):
            raise ValueError(f"VAD {name} 必須介於 {lower}～{upper}" + ("，並使用整數" if integer else ""))
        result[name] = int(value) if integer else float(value)
    if engine == "silero" and result["max_gain_db"] != 0:
        raise ValueError("Silero 方案不支援偵測增益，請設為 0")
    return {**INTERNAL, **result}
