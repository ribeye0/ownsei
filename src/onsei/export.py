from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from filelock import FileLock

import pysubs2

from onsei.storage import Store, ConflictError
from onsei.snapshots import register_export, cue_digest, export_key
from onsei.util import slug
from onsei.translation_contract import polluted
from onsei.webvtt import VttCue, write_vtt


def export_track(
    store: Store,
    track_id: str,
    directory: Path,
    japanese_only=False,
    *,
    version=None,
    result_id=None,
    languages=None,
    formats=None,
) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(store.path / f".export-{track_id}.lock", timeout=30):
        store.ensure_legacy_result(track_id)

        def read(db=None):
            if version is None and result_id is None:
                return store.read_result(db, track_id) if db is not None else store.result(track_id)
            from onsei.domain import Cue, Track
            from onsei.storage import read_cue

            snapshot = store.input_snapshot(
                track_id, version, result_id, db, diagnostics=formats is None or "jsonl" in formats
            )
            track = Track.model_validate({**snapshot["track"], "active_version": snapshot["version"]})
            return track, [read_cue(Cue.model_validate(c).model_dump_json()) for c in snapshot["cues"]]

        track, cues = read()
        identity = cues[0].provenance.get("translation_version") if cues else None
        metadata = store.run_metadata(identity) if identity else {}
        if not japanese_only and (
            metadata.get("status") == "stale"
            or (metadata.get("stage") == "review" and metadata.get("status") != "completed")
        ):
            raise ValueError("來源待更新或全文審校尚未完成；請先完成有效結果再匯出中文")
        failure = None
        with TemporaryDirectory(prefix=".export-", dir=directory) as work:
            try:
                _render(track, cues, Path(work), japanese_only, languages, formats)
            except ValueError as error:
                failure = error  # Keep useful audit/text files when subtitles are blocked.
            staged = sorted(Path(work).iterdir())
            if staged:
                with store.db(write=True) as db:
                    current_track, current = read(db)
                    current_metadata = store.run_metadata(identity, db) if identity else {}
                    if (
                        current_track.active_version != track.active_version
                        or export_key(track_id, current) != export_key(track_id, cues)
                        or cue_digest(current) != cue_digest(cues)
                        or (not japanese_only and current_metadata.get("status") == "stale")
                        or (
                            not japanese_only
                            and current_metadata.get("stage") == "review"
                            and current_metadata.get("status") != "completed"
                        )
                    ):
                        raise ConflictError("匯出期間結果已更新，請重新匯出目前結果。")
                    # Hide the old manifest before replacing any files. If publishing
                    # fails midway, no partially replaced bundle is advertised.
                    db.execute("DELETE FROM settings WHERE key=?", (export_key(track_id, cues),))
                    paths = []
                    try:
                        for temporary in staged:
                            target = directory / temporary.name
                            temporary.replace(target)
                            paths.append(target)
                    except OSError:
                        db.commit()  # Persist manifest removal even when a file operation fails.
                        raise
                    register_export(store, track_id, cues, [p.name for p in paths], db=db)
            else:
                paths = []
        if failure:
            raise failure
        return paths


def _render(track, cues, directory: Path, japanese_only, languages=None, formats=None, audit_cues=None):
    if not cues:
        raise ValueError("No transcript to export")
    directory.mkdir(parents=True, exist_ok=True)
    result = cues[0].provenance.get("translation_version", "legacy")
    prefix = directory / f"{slug(track.name)}-{track.id[:8]}-{cues[0].version[:8]}-{result[:8]}"
    languages = set(("ja", "zh", "bilingual") if languages is None else languages)
    formats = set(("jsonl", "txt", "vtt", "srt", "ass", "lrc") if formats is None else formats)
    if japanese_only:
        languages &= {"ja"}

    def save(suffix, text):
        Path(f"{prefix}.{suffix}").write_text(text, encoding="utf-8")

    if "jsonl" in formats:
        with Path(f"{prefix}.jsonl").open("w", encoding="utf-8") as file:
            for cue in cues if audit_cues is None else audit_cues:
                file.write(
                    json.dumps({**cue.model_dump(), "ja": cue.ja, "zh": cue.zh}, ensure_ascii=False) + "\n"
                )
    if "ja" in languages and "txt" in formats:
        save("ja.txt", "\n".join(c.ja for c in cues) + "\n")
    visible = [c for c in cues if c.ja.strip() or (not japanese_only and c.zh and c.zh.strip())]
    if not visible:
        raise ValueError("No visible subtitle text")
    error = None
    if not japanese_only:
        missing = sum(
            not c.zh
            or c.translation_status in ("pending", "stale", "failed")
            or (c.zh_override is None and polluted(c.zh_auto))
            for c in cues
        )
        if missing:
            error = ValueError(f"{missing} translations missing/stale; complete translation first.")
            languages &= {"ja"}
    target = cues[0].provenance.get("translation", {}).get("target_language", "zh-Hant")
    zh = "zh-Hans" if target == "zh-Hans" else "zh-TW"
    if "txt" in formats:
        if "zh" in languages:
            save(f"{zh}.txt", "\n".join(c.zh for c in cues) + "\n")
        if "bilingual" in languages:
            save("bilingual.txt", "\n\n".join(f"{c.ja}\n{c.zh}" for c in cues) + "\n")
    timed = formats & {"srt", "vtt", "ass", "lrc"}
    if timed and any(
        c.timing_status in ("stale", "alignment_failed") or c.end_ms > track.duration_ms for c in visible
    ):
        raise ValueError("Subtitle timing needs review; listen and correct the timestamps before exporting.")
    for language in sorted(languages) if timed else ():
        rows = []
        for c in visible:
            text = (
                c.ja
                if language == "ja"
                else c.zh
                if language == "zh"
                else "\n".join(t for t in (c.ja, c.zh) if t and t.strip())
            )
            if text and text.strip():
                rows.append(VttCue(c.start_ms, c.end_ms, text, c.id))
        label = zh if language == "zh" else language
        if "vtt" in timed:
            save(f"{label}.vtt", write_vtt(rows))
        if "lrc" in timed:
            save(f"{label}.lrc", write_lrc(rows))
        if timed & {"srt", "ass"}:
            subs = pysubs2.SSAFile()
            subs.styles["Default"].fontname = "Noto Sans CJK TC"
            subs.styles["Default"].fontsize = 24
            for row in rows:
                subs.append(
                    pysubs2.SSAEvent(start=row.start_ms, end=row.end_ms, text=row.text.replace("\n", "\\N"))
                )
            for kind in sorted(timed & {"srt", "ass"}):
                save(f"{label}.{kind}", subs.to_string(kind))
    if error:
        raise error


def write_lrc(cues: list[VttCue]) -> str:
    lines = []
    for cue in sorted(cues, key=lambda cue: cue.start_ms):
        text = " / ".join(line.strip() for line in cue.text.splitlines() if line.strip())
        if not text:
            continue
        centiseconds = (cue.start_ms + 5) // 10
        minutes, rest = divmod(centiseconds, 6000)
        seconds, fraction = divmod(rest, 100)
        lines.append(f"[{minutes:02d}:{seconds:02d}.{fraction:02d}]{text}")
    return "\n".join(lines) + ("\n" if lines else "")
