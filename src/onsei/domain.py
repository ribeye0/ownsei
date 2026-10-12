from __future__ import annotations

from typing import Any
import re

from pydantic import BaseModel, Field, model_validator


class Track(BaseModel):
    id: str
    name: str
    source: str
    sha256: str
    duration_ms: int
    sample_rate: int
    channels: int
    active_version: str | None = None
    source_signature: list[int] | None = None


class Cue(BaseModel):
    id: str
    track_id: str
    version: str
    ordinal: int
    start_ms: int
    end_ms: int
    ja_raw: str
    ja_override: str | None = None
    zh_auto: str | None = None
    zh_override: str | None = None
    revision: int = 0
    timing_status: str = "asr"
    translation_status: str = "pending"
    review_flags: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @property
    def ja(self) -> str:
        if self.ja_override is not None:
            return self.ja_override
        # Subtitle presentation only: retain the recognition text for audit.
        # Preserve ellipses, decimal points, questions and human punctuation.
        return subtitle_ja(self.ja_raw)

    @property
    def zh(self) -> str | None:
        if self.zh_override is not None:
            return self.zh_override
        return subtitle_zh(self.zh_auto) if self.zh_auto is not None else None

    @model_validator(mode="after")
    def valid_time(self) -> Cue:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("Cue needs nonnegative start and end > start")
        return self


def subtitle_ja(text: str) -> str:
    return re.sub(r"(?<![.．])[。．.](?=[」』\"）)]*\s*$)", "", text)


def subtitle_zh(text: str) -> str:
    """Render machine subtitles without terminal stops/commas; keep raw output.

    Apply to each displayed line, including punctuation inside closing quotes.
    Questions, exclamations, ellipses and punctuation within a line stay intact.
    """
    pattern = r"(?:[。，,、]|(?<![.．])[.．](?![.．]))+(?=[」』\"'”’）)】〕〉》\]]*[^\S\n]*$)"
    while (cleaned := re.sub(pattern, "", text, flags=re.MULTILINE)) != text:
        text = cleaned
    return text
