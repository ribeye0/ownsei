"""FireRedVAD ncnn CPU analysis for input segmentation and optional cue filtering."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time
import wave

from onsei.util import atomic_json as save
from onsei.catalog import REGISTRY
from onsei.vad_settings import normalize_settings

REVISION = REGISTRY["firered-vad"][1]
HASHES = {
    "cmvn_istd.bin": "0f577337a74e728118f323ea6a64d269de85d29d40f4a60b57cabc960ca13add",
    "cmvn_means.bin": "3c80c1c51be7d363f1d64a1d52687df163f60a060384058a83422a22bd80b5c7",
    "firered_vad_non_stream.ncnn.bin": "961c27ff99ec8474048e3ba682d4af07315e6ac4638ad1de20675240c36b43ff",
    "firered_vad_non_stream.ncnn.param": "9e361f9af4d9f94c879c75a91ba594fe612100dddd6d6fa657a748ff70e34704"
}

CONFIG = normalize_settings("firered")


class Intervals:
    def __init__(self, duration_ms, config=CONFIG):
        self.duration = duration_ms
        self.config = config
        self.start = self.pending = None
        self.raw = []

    def feed(self, probability, center_ms):
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid VAD probability")
        a, b = max(0, center_ms - 5), min(self.duration, center_ms + 5)
        if probability >= self.config["threshold"]:
            if self.start is None:
                self.start = a
            self.pending = None
        elif self.start is not None and probability < max(0.01, self.config["threshold"] - 0.15):
            if self.pending is None:
                self.pending = a
            if b - self.pending >= self.config["min_silence_ms"]:
                self.close(self.pending)

    def close(self, end):
        if self.start is not None and end - self.start >= self.config["min_speech_ms"]:
            self.raw.append((self.start, end))
        self.start = self.pending = None

    def finish(self):
        self.close(self.duration if self.pending is None else self.pending)
        merged = []
        for a, b in self.raw:
            a, b = (
                max(0, round(a - self.config["pad_ms"])),
                min(self.duration, round(b + self.config["pad_ms"])),
            )
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(b, merged[-1][1])
            else:
                merged.append([a, b])
        return merged


def bounded_regions(spans, probabilities, limit_ms=CONFIG["max_region_ms"]):
    """Split long positive spans at low-probability frames; never drop their audio."""
    import numpy as np

    result = []
    previous = 0
    for a, b in spans:
        if not 0 <= previous <= a < b:
            raise ValueError("Invalid VAD interval order/bounds")
        previous = b
        while b - a > limit_ms:
            lower, upper = a + max(1000, limit_ms - 5000), a + limit_ms
            first, last = (
                max(0, math.ceil((lower - 12.5) / 10)),
                min(len(probabilities), math.floor((upper - 12.5) / 10) + 1),
            )
            if last > first:
                window = probabilities[first:last]
                # Prefer a late cut among equally good silent positions.
                index = last - 1 - int(np.argmin(window[::-1]))
                cut = round(12.5 + index * 10)
            else:
                cut = upper
            cut = min(upper, max(lower, cut))
            result.append({"start": a / 1000, "end": cut / 1000})
            a = cut
        result.append({"start": a / 1000, "end": b / 1000})
    return result


def detect(path, model_dir, output, config=None, limit_ms=None):
    config = normalize_settings("firered", config)
    import numpy as np
    from onsei.vad_ncnn import Detector

    for name, expected in HASHES.items():
        with (model_dir / name).open("rb") as file:
            actual = hashlib.file_digest(file, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"FireRedVAD checksum mismatch: {name}")
    detector = Detector(model_dir)
    started = time.monotonic()
    blocks = []
    with wave.open(str(path)) as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise ValueError("FireRedVAD requires mono 16 kHz PCM16")
        duration = min(audio.getnframes() / 16, limit_ms) if limit_ms else audio.getnframes() / 16
        collector = Intervals(duration, config)
        # One value per original 10 ms feature grid, independent of block boundaries.
        probabilities = np.zeros(math.ceil(duration / 10), dtype=np.float32)
        for owned_start in range(0, math.ceil(duration), config["block_ms"]):
            owned_end = min(duration, owned_start + config["block_ms"])
            start = max(0, owned_start - config["context_ms"])
            end = min(duration, owned_end + config["context_ms"])
            audio.setpos(round(start * 16))
            values = np.frombuffer(audio.readframes(round((end - start) * 16)), np.int16).astype(np.float32)
            peak = float(np.max(np.abs(values), initial=0)) / 32768
            gain = (
                min(10 ** (config["max_gain_db"] / 20), max(1.0, config["gain_peak"] / peak))
                if peak > 1e-5
                else 1.0
            )
            local = np.zeros(max(0, int((len(values) - 400) // 160 + 1)), dtype=np.float32)
            if peak > 1e-5 and len(values) >= 400:
                pcm = np.clip(np.rint(values * gain), -32768, 32767).astype(np.int16)
                local = detector.probabilities(pcm)
            first = max(0, math.ceil((owned_start - 12.5) / 10))
            last = min(len(probabilities), math.ceil((owned_end - 12.5) / 10))
            offset = round(start / 10)
            for index in range(first, last):
                local_index = index - offset
                probability = float(local[local_index]) if 0 <= local_index < len(local) else 0.0
                probabilities[index] = probability
                collector.feed(probability, 12.5 + index * 10)
            blocks.append(
                {"start_ms": owned_start, "end_ms": owned_end, "peak": peak, "detection_gain": gain}
            )
            print(
                json.dumps(
                    {
                        "stage": "vad",
                        "message": f"FireRedVAD：{owned_end / 1000:.1f}/{duration / 1000:.1f} 秒（CPU）",
                    }
                ),
                flush=True,
            )
    spans = collector.finish()
    regions = bounded_regions(spans, probabilities, config["max_region_ms"])
    np.save(output.with_suffix(".probabilities.npy"), probabilities)
    result = {
        "engine": "FireRedVAD",
        "runtime": "ncnn CPU",
        "model_revision": REVISION,
        "config": config,
        "duration_seconds": duration / 1000,
        "seconds": time.monotonic() - started,
        "spans_ms": spans,
        "core_spans_ms": [[max(0, round(a)), min(duration, round(b))] for a, b in collector.raw],
        "regions": regions,
        "blocks": blocks,
        "detection_gain_only": True,
    }
    save(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--limit-ms", type=int)
    args = parser.parse_args()
    if args.model is None:
        from onsei.models import model_dir

        args.model = model_dir("firered-vad")
    detect(
        args.audio,
        args.model,
        args.output,
        json.loads(args.settings.read_text()) if args.settings else None,
        args.limit_ms,
    )
