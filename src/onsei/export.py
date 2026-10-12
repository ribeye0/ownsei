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


def export_track(store: Store, track_id: str, directory: Path, japanese_only=False, *, version=None, result_id=None) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(store.path / f".export-{track_id}.lock", timeout=30):
        store.ensure_legacy_result(track_id)
        def read(db=None):
            if version is None and result_id is None:
                return store.read_result(db, track_id) if db is not None else store.result(track_id)
            from onsei.domain import Cue, Track
            from onsei.storage import read_cue
            snapshot = store.input_snapshot(track_id, version, result_id, db)
            track = Track.model_validate({**snapshot["track"], "active_version": snapshot["version"]})
            return track, [read_cue(Cue.model_validate(c).model_dump_json()) for c in snapshot["cues"]]

        track, cues = read()
        identity = cues[0].provenance.get("translation_version") if cues else None
        metadata = store.run_metadata(identity) if identity else {}
        if not japanese_only and (metadata.get("status") == "stale" or
                                 (metadata.get("stage") == "review" and metadata.get("status") != "completed")):
            raise ValueError("來源待更新或全文審校尚未完成；請先完成有效結果再匯出中文")
        failure = None
        with TemporaryDirectory(prefix=".export-", dir=directory) as work:
            try:
                _render(track, cues, Path(work), japanese_only)
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
                        or (not japanese_only and current_metadata.get("stage") == "review"
                            and current_metadata.get("status") != "completed")
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


def _render(track, cues, directory: Path, japanese_only):
    if not cues:
        raise ValueError("No transcript to export")
    directory.mkdir(parents=True, exist_ok=True)
    result = cues[0].provenance.get("translation_version", "legacy")
    prefix = directory / f"{slug(track.name)}-{track.id[:8]}-{cues[0].version[:8]}-{result[:8]}"
    paths = []

    def save(suffix, text):
        p = Path(f"{prefix}.{suffix}")
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
        paths.append(p)

    save(
        "jsonl",
        "".join(
            json.dumps({**c.model_dump(), "ja": c.ja, "zh": c.zh}, ensure_ascii=False) + "\n" for c in cues
        ),
    )
    save("ja.txt", "\n".join(c.ja for c in cues) + "\n")
    good_time = all(
        c.timing_status not in ("stale", "alignment_failed") and c.end_ms <= track.duration_ms for c in cues
    )
    languages = ["ja"]
    failure = None
    if not japanese_only:
        missing = [
            c.id
            for c in cues
            if not c.zh
            or c.translation_status in ("pending", "stale", "failed")
            or (c.zh_override is None and polluted(c.zh_auto))
        ]
        if missing:
            failure = ValueError(f"{len(missing)} translations missing/stale. Japanese/JSONL saved; complete translation first.")
        else:
            save("bilingual.txt", "\n\n".join(f"{c.ja}\n{c.zh}" for c in cues) + "\n")
            target = cues[0].provenance.get("translation", {}).get("target_language", "zh-Hant")
            save(("zh-Hans" if target == "zh-Hans" else "zh-TW") + ".txt", "\n".join(c.zh for c in cues) + "\n")
            save("bilingual.md", f"# {track.name}\n\n" + "\n\n".join(f"{c.ja}\n\n{c.zh}" for c in cues) + "\n")
            if good_time:
                save(
                    "bilingual-timed.md",
                    f"# {track.name}\n\n"
                    + "\n\n".join(
                        f"[{pysubs2.time.ms_to_str(c.start_ms)} → {pysubs2.time.ms_to_str(c.end_ms)}]\n\n{c.ja}\n\n{c.zh}"
                        for c in cues
                    )
                    + "\n",
                )
            target = cues[0].provenance.get("translation", {}).get("target_language", "zh-Hant")
            languages += ["zh-Hans" if target == "zh-Hans" else "zh-TW", "bilingual"]
    if not good_time:
        raise ValueError(
            "Transcript saved. Subtitle export blocked by stale/failed alignment; realign first."
        )
    for language in languages:
        subs = pysubs2.SSAFile()
        vtt = []
        subs.styles["Default"].fontname = "Noto Sans CJK TC"
        subs.styles["Default"].fontsize = 24
        for c in cues:
            text = c.ja if language == "ja" else c.zh if language in ("zh-TW", "zh-Hans") else f"{c.ja}\n{c.zh}"
            vtt.append(VttCue(c.start_ms, c.end_ms, text, c.id))
            subs.append(pysubs2.SSAEvent(start=c.start_ms, end=c.end_ms, text=text.replace("\n", "\\N")))
        save(f"{language}.srt", subs.to_string("srt"))
        save(f"{language}.vtt", write_vtt(vtt))
        if language == "bilingual":
            save("bilingual.ass", subs.to_string("ass"))
    if failure:
        raise failure
    return paths
