"""Isolated GPU worker. Its imports intentionally do not depend on the app environment."""

from __future__ import annotations

import json
import os
import sys
import time
import wave
from pathlib import Path


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
    started = time.monotonic()
    if kind == "whisper":
        from faster_whisper import WhisperModel

        compute_type, multilingual, whisper_options = whisper_settings(checkpoint)
        model = WhisperModel(checkpoint, device="cuda", compute_type=compute_type, num_workers=1)
        if multilingual and not model.model.is_multilingual:
            if model.hf_tokenizer.token_to_id("<|ja|>") is None:
                raise ValueError("Whisper generation config requires a missing Japanese language token")
            model.model = MultilingualWhisper(model.model)
        print(
            json.dumps({"compute_type": compute_type, "multilingual": model.model.is_multilingual}),
            flush=True,
        )
    elif kind == "cohere":
        import torch
        import importlib.util
        from tokenizers import Tokenizer

        source = Path(os.environ["ONSEI_ROOT"]) / ".tools/cohere-source/cohere_asr.py"
        spec = importlib.util.spec_from_file_location("cohere_impl", source)
        impl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(impl)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required")
        torch.set_num_threads(2)
        model = impl.CohereAsr.from_pretrained(checkpoint, device="cuda", dtype=torch.bfloat16)
        feature_extractor = impl.CohereAsrFeatureExtractor(checkpoint, device="cpu")
        tokenizer = Tokenizer.from_file(str(Path(checkpoint) / "tokenizer.json"))
        prompt = tokenizer.encode(
            "<|startofcontext|><|startoftranscript|><|emo:undefined|>"
            "<|ja|><|ja|><|pnc|><|noitn|><|notimestamp|><|nodiarize|>",
            add_special_tokens=False,
        ).ids
        prompts = torch.tensor(prompt, dtype=torch.int64, device="cuda")[None, :]
    else:
        import torch
        from qwen_asr import Qwen3ForcedAligner

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required")
        model = Qwen3ForcedAligner.from_pretrained(checkpoint, dtype=torch.bfloat16, device_map="cuda:0")
    for item in request["items"]:
        p = Path(item["output"])
        if p.exists():
            continue
        samples, rate = audio(request["audio"], item["start_ms"], item["end_ms"])
        t = time.monotonic()
        diagnostics = {}
        if kind == "whisper":
            from faster_whisper.vad import get_speech_timestamps, VadOptions

            speech = get_speech_timestamps(
                samples,
                VadOptions(
                    threshold=0.3, min_speech_duration_ms=100, min_silence_duration_ms=800, speech_pad_ms=500
                ),
                sampling_rate=rate,
            )
            diagnostics["vad_speech_ms"] = sum((s["end"] - s["start"]) * 1000 / rate for s in speech)
            diagnostics["digital_silence"] = bool(abs(samples).max(initial=0) < 1e-5)
            segments, _ = model.transcribe(
                samples,
                language="ja",
                task="transcribe",
                beam_size=3,
                condition_on_previous_text=False,
                word_timestamps=True,
                vad_filter=False,
                vad_parameters={
                    "threshold": 0.3,
                    "min_speech_duration_ms": 100,
                    "min_silence_duration_ms": 800,
                    "speech_pad_ms": 500,
                },
                **whisper_options,
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
                for s in (segments if not diagnostics["digital_silence"] else [])
            ]
        elif kind == "cohere":
            import numpy as np

            if np.max(np.abs(samples), initial=0) < 1e-5:
                data = {"text": ""}
            else:
                with torch.inference_mode():
                    if rate != feature_extractor.filterbank.sample_rate:
                        raise ValueError("Cohere input sample rate does not match the feature extractor")
                    # The upstream optional sampling_rate guard references a misspelled attribute.
                    features, lengths = feature_extractor([samples])
                    generated = model.generate(
                        input_ids=prompts.clone(),
                        input_features=features.to("cuda", dtype=torch.bfloat16),
                        lengths=lengths.to("cuda", dtype=torch.int32),
                        max_len=448,
                        flex_attn=False,
                    )
                    text = tokenizer.decode_batch(generated.tolist())[0].strip()
                data = {"text": text}
        else:
            result = model.align(audio=(samples, rate), text=item["text"], language="Japanese")[0]
            data = [{"text": w.text, "start": float(w.start_time), "end": float(w.end_time)} for w in result]
        write(
            p,
            {
                "data": data,
                "seconds": time.monotonic() - t,
                "kind": kind,
                "chunk": item,
                "model": checkpoint,
                "diagnostics": diagnostics,
            },
        )
        print(json.dumps({"completed": str(p), "seconds": time.monotonic() - started}), flush=True)
    if kind != "whisper":
        print(json.dumps({"peak_vram_mb": torch.cuda.max_memory_allocated() / 1024**2}), flush=True)


if __name__ == "__main__":
    main()
