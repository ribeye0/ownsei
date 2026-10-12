"""Execution settings only: never weaken the translation/output contract."""

import os
from dataclasses import asdict, dataclass


# Populated only for model/profile pairs covered by local CUDA acceptance runs.
AUTO_ACCELERATION: dict[str, str] = {"index-translate-9b": "draft-mtp"}
MODES = {"none": "none", "mtp": "draft-mtp", "ngram": "ngram-simple"}


@dataclass(frozen=True)
class DecodingProfile:
    speculative: str = "none"
    draft_tokens: int = 3

    def __post_init__(self):
        if self.speculative not in MODES.values():
            raise ValueError("Unsupported translation acceleration")
        if type(self.draft_tokens) is not int or not 1 <= self.draft_tokens <= 8:
            raise ValueError("Translation draft token count must be between 1 and 8")

    def flags(self):
        if self.speculative == "none":
            return []
        return ["--spec-type", self.speculative, "--spec-draft-n-max", str(self.draft_tokens)]

    def metadata(self):
        return asdict(self)


def decoding_profile(model: str, speculative: str | None = None, draft_tokens: int = 3) -> DecodingProfile:
    if speculative is None:
        mode = os.environ.get("ONSEI_TRANSLATION_ACCELERATION", "auto")
        if mode == "auto":
            speculative = AUTO_ACCELERATION.get(model, "none")
        elif mode in MODES:
            speculative = MODES[mode]
        else:
            raise ValueError("ONSEI_TRANSLATION_ACCELERATION must be auto, none, mtp or ngram")
    return DecodingProfile(speculative, draft_tokens)


def inference_summary(requests: list[dict], counters: dict, wall_seconds: float) -> dict:
    timings = [row.get("timings", {}) for row in requests]
    prefill = sum(row.get("prompt_ms", 0) or 0 for row in timings) / 1000
    decode = sum(row.get("predicted_ms", 0) or 0 for row in timings) / 1000
    tokens = sum(row.get("predicted_n", 0) or 0 for row in timings)
    drafted = sum(row.get("draft_n", 0) or 0 for row in timings)
    accepted = sum(row.get("draft_n_accepted", 0) or 0 for row in timings)
    return {
        "requests": len(requests),
        "prefill_seconds": round(prefill, 3),
        "decode_seconds": round(decode, 3),
        "output_tokens": tokens,
        "decode_tokens_per_second": round(tokens / decode, 2) if decode else None,
        "draft_acceptance": round(accepted / drafted, 4) if drafted else None,
        "wall_seconds": round(wall_seconds, 3),
        "saved_lines_per_minute": round(counters.get("saved_lines", 0) * 60 / wall_seconds, 2)
        if wall_seconds
        else None,
        **counters,
    }
