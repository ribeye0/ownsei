"""Isolated GPU worker. Its imports intentionally do not depend on the app environment."""

from __future__ import annotations

import json
import os
import sys
import time
import wave
from pathlib import Path


# CPython's Windows extension loader requires explicit DLL directories.
_dll_directories = []
if sys.platform == "win32":
    site = Path(sys.prefix) / "Lib/site-packages/nvidia"
    for directory in site.glob("*/bin"):
        _dll_directories.append(os.add_dll_directory(str(directory)))


def write(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def audio(path, start_ms, end_ms):
    import numpy as np

    with wave.open(str(path)) as w:
        rate = w.getframerate()
        w.setpos(round(start_ms * rate / 1000))
        values = np.frombuffer(w.readframes(round((end_ms - start_ms) * rate / 1000)), dtype=np.int16)
    return values.astype(np.float32) / 32768, rate


class MultilingualWhisper:
    """CT2's empty-token heuristic can misclassify a custom Japanese vocabulary."""

    is_multilingual = True

    def __init__(self, model):
        self.model = model

    def __getattr__(self, name):
        return getattr(self.model, name)


def whisper_settings(checkpoint):
    path = Path(checkpoint)
    manifest = path / "onsei-model.json"
    compute_type = (
        json.loads(manifest.read_text()).get("compute_type", "float16") if manifest.exists() else "float16"
    )
    generation = path / "generation_config.json"
    config = json.loads(generation.read_text()) if generation.exists() else {}
    # Never apply standard-vocabulary IDs to efwkjn's remapped Japanese vocabulary.
    options = {"suppress_tokens": config["suppress_tokens"]} if "suppress_tokens" in config else {}
    return compute_type, config.get("is_multilingual", False), options


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    kind, checkpoint = request["kind"], request["model"]
    if kind != "whisper":
        raise ValueError("Unsupported ASR worker")
    from faster_whisper import WhisperModel
    import numpy as np

    started = time.monotonic()
    compute_type, multilingual, options = whisper_settings(checkpoint)
    import os

    device = os.environ.get("ONSEI_WHISPER_DEVICE", "cpu")
    if device == "cpu":
        compute_type = "int8"
    model = WhisperModel(checkpoint, device=device, compute_type=compute_type, num_workers=1)
    if multilingual and not model.model.is_multilingual:
        if model.hf_tokenizer.token_to_id("<|ja|>") is None:
            raise ValueError("Whisper generation config requires a missing Japanese language token")
        model.model = MultilingualWhisper(model.model)
    for item in request["items"]:
        output = Path(item["output"])
        if output.exists():
            continue
        samples, rate = audio(request["audio"], item["start_ms"], item["end_ms"])
        before = time.monotonic()
        data = []
        if np.max(np.abs(samples), initial=0) >= 1e-5:
            segments, _ = model.transcribe(
                samples,
                language="ja",
                task="transcribe",
                beam_size=3,
                condition_on_previous_text=False,
                word_timestamps=True,
                vad_filter=False,
                **options,
            )
            data = [
                {
                    "text": s.text.strip(),
                    "start": s.start,
                    "end": s.end,
                    "avg_logprob": s.avg_logprob,
                    "no_speech_prob": s.no_speech_prob,
                    "words": [
                        {"text": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                        for w in (s.words or [])
                    ],
                }
                for s in segments
            ]
        write(
            output,
            {
                "data": data,
                "seconds": time.monotonic() - before,
                "kind": kind,
                "chunk": item,
                "model": checkpoint,
            },
        )
        print(json.dumps({"completed": str(output), "seconds": time.monotonic() - started}), flush=True)


if __name__ == "__main__":
    main()
