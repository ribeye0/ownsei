"""WebVTT interchange, separate from model output and reference speaker metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape, unescape
from pathlib import Path
import re


_TIME = r"(?:\d{2,}:)?\d{2}:\d{2}\.\d{3}"
_TIMING = re.compile(rf"^({_TIME})[ \t]+-->[ \t]+({_TIME})(?:[ \t]+(.*))?$")
_MARKUP = re.compile(r"</?(?:b|i|u|ruby|rt|c(?:\.[\w-]+)*|v|lang)(?:[ \t][^<>]*)?>|<" + _TIME + r">")


def milliseconds(value: str) -> int:
    parts = value.split(":")
    hours, minutes, seconds = (0, *parts) if len(parts) == 2 else parts
    whole, fraction = seconds.split(".")
    if not 0 <= int(minutes) < 60 or not 0 <= int(whole) < 60:
        raise ValueError(f"Invalid WebVTT timestamp: {value}")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(whole)) * 1000 + int(fraction)


def timestamp(value: int) -> str:
    if type(value) is not int or value < 0:
        raise ValueError("WebVTT time must be nonnegative integer milliseconds")
    hours, rest = divmod(value, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, fraction = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{fraction:03}"


@dataclass
class VttCue:
    start_ms: int
    end_ms: int
    text: str
    identifier: str | None = None
    settings: str = ""

    def reference_text(self, speaker_names: tuple[str, ...] = ()) -> tuple[str, str | None]:
        voices = list(dict.fromkeys(re.findall(r"<v[ \t]+([^<>]+)>", self.text)))
        text = re.sub(r"<rt>.*?</rt>", "", self.text, flags=re.DOTALL)
        text = unescape(_MARKUP.sub("", text))
        speaker = voices[0] if len(voices) == 1 else None
        for name in speaker_names:
            match = re.match(re.escape(name) + r"[：:][ \t]*", text)
            if match:
                text, speaker = text[match.end():], name
                break
        return text, speaker


@dataclass
class VttDocument:
    cues: list[VttCue] = field(default_factory=list)
    header: str = "WEBVTT"
    blocks: list[str] = field(default_factory=list)


def parse_vtt(content: str) -> VttDocument:
    content = content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n", content.strip("\n"))
    if not blocks or not re.fullmatch(r"WEBVTT(?:[ \t][^\n]*)?", blocks[0]):
        raise ValueError("Missing WebVTT header or header separator")
    document = VttDocument(header=blocks[0])
    previous_start = -1
    for block in blocks[1:]:
        if not block.strip():
            continue
        lines = block.splitlines()
        if re.match(r"^NOTE(?:[ \t]|$)", lines[0]) or lines[0] in ("STYLE", "REGION"):
            document.blocks.append(block)
            continue
        offset = 0 if "-->" in lines[0] else 1
        match = _TIMING.fullmatch(lines[offset]) if len(lines) > offset else None
        if not match:
            raise ValueError(f"Invalid WebVTT cue timing: {lines[:2]}")
        start, end = milliseconds(match[1]), milliseconds(match[2])
        if end <= start or start < previous_start:
            raise ValueError("WebVTT cues need increasing start times and positive intervals")
        previous_start = start
        document.cues.append(VttCue(start, end, "\n".join(lines[offset + 1:]),
                                     lines[0] if offset else None, match[3] or ""))
    return document


def read_vtt(path: Path) -> VttDocument:
    return parse_vtt(path.read_text(encoding="utf-8-sig"))


def write_vtt(cues: list[VttCue]) -> str:
    """Generate plain subtitle text; never interpret model text as markup."""
    blocks = ["WEBVTT"]
    previous_start = -1
    for cue in cues:
        start, end = timestamp(cue.start_ms), timestamp(cue.end_ms)
        if cue.end_ms <= cue.start_ms or cue.start_ms < previous_start:
            raise ValueError("WebVTT cues need ordered starts and positive intervals")
        previous_start = cue.start_ms
        if not cue.text.strip() or re.search(r"\n[ \t]*\n", cue.text):
            raise ValueError("WebVTT cue text must be nonempty, without empty internal lines")
        identifier = cue.identifier or ""
        if "\n" in identifier or "\r" in identifier or "-->" in identifier:
            raise ValueError("Invalid WebVTT cue identifier")
        if "\n" in cue.settings or "\r" in cue.settings:
            raise ValueError("Invalid WebVTT cue settings")
        timing = f"{start} --> {end}" + (f" {cue.settings}" if cue.settings else "")
        text = escape(cue.text.replace("\r\n", "\n").replace("\r", "\n"), quote=False)
        blocks.append((identifier + "\n" if identifier else "") + timing + "\n" + text)
    return "\n\n".join(blocks) + "\n"
