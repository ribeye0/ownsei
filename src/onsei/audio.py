from __future__ import annotations

import json
import subprocess
import wave
import uuid
import unicodedata
from pathlib import Path
from typing import Callable

from filelock import FileLock

from onsei.domain import Track
from onsei.storage import Store
from onsei.util import atomic_json, file_hash, natural_key
from onsei.source import signature as _signature, validate_source

FORMATS = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus"}


def probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    d = json.loads(r.stdout)
    a = next(s for s in d["streams"] if s["codec_type"] == "audio")
    return {
        "duration_ms": round(float(d["format"]["duration"]) * 1000),
        "sample_rate": int(a["sample_rate"]),
        "channels": a["channels"],
    }


# Only whole folder names that explicitly denote an encoding are interchangeable.
# Other directory names remain part of the identity (disc, edition, work, etc.).
_FORMAT_FOLDERS = {"wav", "mp3", "wave", "wav版", "mp3版", "wav音源", "mp3音源", "wav版本", "mp3版本"}


def select_audio_variants(paths: list[Path], root: Path) -> tuple[list[Path], int]:
    groups: dict[tuple[str, ...], list[Path]] = {}
    for path in paths:
        parts = path.relative_to(root).parts
        key = tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in parts[:-1]
            if part.casefold() not in _FORMAT_FOLDERS
        ) + (unicodedata.normalize("NFC", path.stem),)
        groups.setdefault(key, []).append(path)
    skipped = set()
    for group in groups.values():
        wavs = [p for p in group if p.suffix.lower() == ".wav"]
        mp3s = [p for p in group if p.suffix.lower() == ".mp3"]
        # Ambiguous groups are deliberately kept rather than guessing a disc/version.
        if len(wavs) != 1 or not mp3s:
            continue
        duration = probe(wavs[0])["duration_ms"]
        for mp3 in mp3s:
            if abs(probe(mp3)["duration_ms"] - duration) <= 1000:
                skipped.add(mp3)
    return [p for p in paths if p not in skipped], len(skipped)


def import_audio(
    store: Store, source: Path, prefer_wav: bool = True, progress: Callable[[dict], None] | None = None
) -> list[Track]:
    # Serialize both CLI and background imports, including checksum cache updates.
    if progress:
        progress({"stage": "waiting"})
    with FileLock(store.path / ".import.lock"):
        return _import_audio(store, source, prefer_wav, progress)


def _import_audio(store: Store, source: Path, prefer_wav: bool, progress) -> list[Track]:
    source = source.resolve()
    state = {
        "stage": "scanning",
        "files_total": 0,
        "files_done": 0,
        "bytes_total": 0,
        "bytes_done": 0,
        "bytes_read": 0,
        "added": 0,
        "skipped": 0,
        "cached": 0,
        "variants_skipped": 0,
        "current_file": "",
    }

    def report(stage: str, **changes):
        state.update(stage=stage, **changes)
        if progress:
            progress(dict(state))

    report("scanning")
    if not source.exists():
        raise ValueError("Audio path does not exist")
    paths = (
        [source]
        if source.is_file()
        else sorted(
            (p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in FORMATS),
            key=lambda p: natural_key(str(p)),
        )
    )
    if prefer_wav and source.is_dir():
        paths, variants_skipped = select_audio_variants(paths, source)
        report("scanning", variants_skipped=variants_skipped)
    sizes = [p.stat().st_size for p in paths]
    report("scanning", files_total=len(paths), bytes_total=sum(sizes))
    cache_path = store.path / "import-checksums.json"
    try:
        cache = json.loads(cache_path.read_text())
        if not isinstance(cache, dict):
            cache = {}
    except (FileNotFoundError, ValueError):
        cache = {}
    known = {t.sha256: t for t in store.tracks()}
    added = []
    for index, (p, size) in enumerate(zip(paths, sizes)):
        report("hashing", current_file=str(p))
        if p.suffix.lower() not in FORMATS:
            raise ValueError(f"Unsupported audio: {p.name}")
        before = _signature(p)
        entry = cache.get(str(p), {})
        base_done, base_read = state["bytes_done"], state["bytes_read"]
        if (
            isinstance(entry, dict)
            and entry.get("signature") == before
            and isinstance(entry.get("sha256"), str)
        ):
            h = entry["sha256"]
            report("cached", cached=state["cached"] + 1, bytes_done=base_done + size)
        else:

            def hash_progress(done):
                report("hashing", bytes_done=base_done + min(done, size), bytes_read=base_read + done)

            h = file_hash(p, progress=hash_progress)
        if _signature(p) != before:
            raise ValueError(f"匯入期間音檔已變更，請完成檔案寫入後重試：{p}")
        if h in known:
            report("duplicate", skipped=state["skipped"] + 1)
        else:
            report("probing")
            metadata = probe(p)
            if _signature(p) != before:
                raise ValueError(f"匯入期間音檔已變更：{p}")
            report("saving")
            t = Track(id=h[:24], name=p.stem, source=str(p), sha256=h, source_signature=before, **metadata)
            store.save_track(t)
            known[h] = t
            added.append(t)
            report("saving", added=len(added))
        cache[str(p)] = {"signature": before, "sha256": h}
        atomic_json(cache_path, cache)
        report("checked", files_done=index + 1, bytes_done=base_done + size)
    report("completed")
    return added


def pcm(store: Store, track: Track) -> Path:
    validate_source(store, track)
    with FileLock(store.path / f".pcm-{track.id}.lock", timeout=30):
        return _pcm(store, track)


def _pcm(store: Store, track: Track) -> Path:
    p = store.path / "audio" / f"{track.id}.wav"
    if p.exists():
        try:
            with wave.open(str(p)) as audio:
                if (
                    audio.getframerate() == 16000
                    and audio.getnchannels() == 1
                    and audio.getsampwidth() == 2
                    and audio.getnframes() > 0
                ):
                    return p
        except (wave.Error, EOFError):
            pass
        p.replace(p.with_name(f"{p.name}.{uuid.uuid4().hex}.invalid"))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.stem}.{uuid.uuid4().hex}.tmp.wav")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-i",
                track.source,
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(tmp),
            ],
            check=True,
            timeout=max(120, track.duration_ms / 1000 * 2),
        )
        validate_source(store, track)
        tmp.replace(p)
    finally:
        tmp.unlink(missing_ok=True)
    return p


def playback_source(store: Store, track: Track) -> Path:
    validate_source(store, track)
    source = Path(track.source)
    if source.suffix.lower() in (".wav", ".mp3", ".ogg", ".m4a"):
        return source
    proxy = store.path / "audio" / f"{track.id}-stereo.mp3"
    proxy.parent.mkdir(exist_ok=True)
    with FileLock(store.path / f".playback-{track.id}.lock", timeout=30):
        if not proxy.exists():
            tmp = proxy.with_name(f"{proxy.stem}.{uuid.uuid4().hex}.tmp.mp3")
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-nostdin",
                        "-y",
                        "-i",
                        str(source),
                        "-vn",
                        "-c:a",
                        "libmp3lame",
                        "-q:a",
                        "2",
                        str(tmp),
                    ],
                    check=True,
                    timeout=max(120, track.duration_ms / 1000 * 2),
                )
                validate_source(store, track)
                tmp.replace(proxy)
            finally:
                tmp.unlink(missing_ok=True)
    return proxy


def chunks(path: Path, limit_ms: int | None = None) -> list[dict]:
    with wave.open(str(path)) as w:
        total = round(w.getnframes() / w.getframerate() * 1000)
    total = min(total, limit_ms) if limit_ms else total
    return [
        {
            "index": i,
            "start_ms": max(0, start - 500),
            "end_ms": min(total, start + 24500),
            "owned_start_ms": start,
            "owned_end_ms": min(total, start + 24000),
        }
        for i, start in enumerate(range(0, total, 24000))
    ]
