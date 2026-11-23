"""Version-pinned downloads and optional copies beside original audio files."""

import json
import os
import shutil
import time
import uuid
import zipfile
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from opencc import OpenCC

from onsei.domain import Cue, Track
from onsei.export import _render
from onsei.snapshots import cue_digest
from onsei.util import atomic_json, slug

FORMATS = {"vtt", "srt", "lrc", "ass", "txt", "jsonl"}
LANGUAGES = {"ja", "zh", "bilingual"}
DESTINATIONS = {"download", "source", "sub"}


def audio_stem(track):
    # No slug, Unicode normalization, trimming or title substitution: combining
    # dakuten, whitespace and punctuation are part of the original filename.
    return Path(track.source).stem


def output_name(track, language, kind, language_code, language_tag=False):
    label = {"zh-Hant": "zh-tw", "zh-Hans": "zh-cn"}.get(language_code, "zh-tw")
    if language not in ("zh-TW", "zh-Hans", "bilingual"):
        label = language
    tag = "." + label if language_tag and language != "audit" else ""
    return audio_stem(track) + tag + "." + kind


def publish_copy(source, target, overwrite=False):
    """Stage in the target filesystem; exclusive linking never clobbers a file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(prefix=".ownsei-", dir=target.parent, delete=False) as work:
            temporary = Path(work.name)
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, work)
        if overwrite:
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise ValueError("拒絕覆寫符號連結或非一般檔案")
            os.replace(temporary, target)
        else:
            os.link(temporary, target)  # Atomic no-replace, including a concurrent writer.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_delivery(
    store,
    selections,
    languages,
    formats,
    chinese="as-result",
    *,
    language_tag=False,
    destination="download",
    overwrite=False,
):
    if not selections or not languages or not formats:
        raise ValueError("請選擇音軌、語言及格式")
    if (
        set(languages) - LANGUAGES
        or set(formats) - FORMATS
        or chinese not in ("as-result", "zh-Hant", "zh-Hans")
        or destination not in DESTINATIONS
        or type(language_tag) is not bool
        or type(overwrite) is not bool
    ):
        raise ValueError("不支援的匯出選項")
    if len({s["track_id"] for s in selections}) != len(selections):
        raise ValueError("音軌不可重複")
    directory = store.path / "exports" / ".downloads"
    directory.mkdir(parents=True, exist_ok=True)
    identity = uuid.uuid4().hex
    report, dependencies, outputs = [], [], []
    root = directory / identity
    root.mkdir()
    archive = directory / (identity + ".zip")
    try:
        for selected in selections:
            entry = {
                "track_id": selected["track_id"],
                "files": [],
                "error": None,
                "written": [],
                "write_errors": [],
            }
            report.append(entry)
            try:
                snapshot = store.input_snapshot(
                    selected["track_id"], selected.get("version"), selected.get("result_id")
                )
                track = Track.model_validate(snapshot["track"])
                entry["name"] = track.name
                entry["source"] = track.source
                cues = [Cue.model_validate(c) for c in snapshot["cues"]]
                metadata = store.run_metadata(snapshot["result_id"])
                if chinese != "as-result":
                    convert = OpenCC("t2s" if chinese == "zh-Hans" else "s2t")
                    for cue in cues:
                        if cue.zh_auto is not None:
                            cue.zh_auto = convert.convert(cue.zh_auto)
                        if cue.zh_override is not None:
                            cue.zh_override = convert.convert(cue.zh_override)
                no_chinese = selected.get("result_id") is None
                zh_blocked = metadata.get("status") == "stale" or (
                    metadata.get("stage") == "review" and metadata.get("status") != "completed"
                )
                with TemporaryDirectory(prefix=".delivery-", dir=directory) as work:
                    try:
                        if (zh_blocked or no_chinese) and set(languages) != {"ja"}:
                            _render(track, cues, Path(work), True)
                            entry["error"] = (
                                "未選中文結果；只匯出有效日文"
                                if no_chinese
                                else "中文來源待更新或審校尚未完成；只匯出有效日文"
                            )
                        else:
                            _render(track, cues, Path(work), set(languages) == {"ja"})
                    except ValueError as error:
                        entry["error"] = str(error)
                    original = [Cue.model_validate(c) for c in snapshot["cues"]]
                    for audit in Path(work).glob("*.jsonl"):
                        audit.write_text(
                            "".join(
                                json.dumps({**c.model_dump(), "ja": c.ja, "zh": c.zh}, ensure_ascii=False)
                                + "\n"
                                for c in original
                            ),
                            encoding="utf-8",
                        )
                    dependency = {
                        "track_id": track.id,
                        "version": snapshot["version"],
                        "result_id": snapshot["result_id"],
                        "digest": cue_digest(original),
                        "status": metadata.get("status"),
                    }
                    verify_dependency(store, dependency)
                    language_code = (
                        chinese
                        if chinese != "as-result"
                        else (
                            metadata.get("target_language")
                            or cues[0].provenance.get("translation", {}).get("target_language", "zh-Hant")
                        )
                    )
                    candidates = []
                    marker = f"-{track.id[:8]}-{cues[0].version[:8]}-{cues[0].provenance.get('translation_version', 'legacy')[:8]}."
                    for path in sorted(Path(work).iterdir()):
                        suffix = path.name.rsplit(marker, 1)[-1]
                        language, kind = ("audit", "jsonl") if suffix == "jsonl" else suffix.split(".", 1)
                        requested = (
                            language == "audit"
                            or {"zh-TW": "zh", "zh-Hans": "zh"}.get(language, language) in languages
                        )
                        if requested and kind in formats:
                            name = output_name(track, language, kind, language_code, language_tag)
                            folder = (
                                {"zh-Hant": "zh-tw", "zh-Hans": "zh-cn"}.get(language_code, "zh-tw")
                                if language in ("zh-TW", "zh-Hans")
                                else language
                            )
                            candidates.append((path, name, folder))
                    counts = Counter(name for _, name, _ in candidates)
                    for path, name, folder in candidates:
                        relative = str(Path(folder) / name) if counts[name] > 1 else name
                        staged = root / str(len(outputs)) / name
                        staged.parent.mkdir()
                        shutil.copyfile(path, staged)
                        outputs.append(
                            {
                                "name": name,
                                "relative": relative,
                                "storage": str(staged.relative_to(root)),
                                "track_id": track.id,
                                "source": track.source,
                                "dependency": dependency,
                            }
                        )
                    if candidates:
                        dependencies.append(dependency)
                    elif not entry["error"]:
                        entry["error"] = "所選語言與格式沒有可匯出的檔案，請查看來源結果與時間狀態"
            except (ValueError, OSError) as error:
                entry["error"] = str(error)
        if not outputs:
            shutil.rmtree(root)
            return {"id": None, "files": [], "downloads": [], "has_zip": False, "tracks": report}
        for dependency in dependencies:
            verify_dependency(store, dependency)
        # Downloads from distinct audio directories can have identical names.
        # Disambiguate archive paths, never alter the actual subtitle basename.
        counts = Counter(o["relative"] for o in outputs)
        for index, output in enumerate(outputs):
            output["index"] = index
            output["archive_path"] = (
                str(Path("track-" + output["track_id"]) / output["relative"])
                if counts[output["relative"]] > 1
                else output["relative"]
            )
            entry = next(e for e in report if e["track_id"] == output["track_id"])
            entry["files"].append(output["archive_path"])
        if destination != "download":
            # Refuse all colliding local targets rather than let selection order
            # decide which subtitle wins, including overwrite mode.
            targets = []
            for output in outputs:
                source = Path(output["source"])
                parent = source.parent / "sub" if destination == "sub" else source.parent
                targets.append(parent / output["relative"])
            target_counts = Counter(str(t.resolve()) for t in targets)
            for output, target in zip(outputs, targets):
                entry = next(e for e in report if e["track_id"] == output["track_id"])
                try:
                    if target_counts[str(target.resolve())] > 1:
                        raise ValueError("多條音軌對應同一字幕路徑，請分開匯出")
                    if not Path(output["source"]).is_file():
                        raise ValueError("原音檔不存在，未寫入字幕")
                    source_parent = Path(output["source"]).parent
                    for parent in [target.parent, *target.parent.parents]:
                        if parent == source_parent:
                            break
                        if parent.is_symlink():
                            raise ValueError("字幕子目錄為符號連結，未寫入字幕")
                    verify_dependency(store, output["dependency"])
                    publish_copy(root / output["storage"], target, overwrite)
                    entry["written"].append(str(target))
                except FileExistsError:
                    entry["write_errors"].append({"path": str(target), "error": "已有同名檔案，保留原檔"})
                except (ValueError, OSError) as error:
                    entry["write_errors"].append({"path": str(target), "error": str(error)})
        has_zip = len(outputs) > 1
        if has_zip:
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for output in outputs:
                    bundle.write(root / output["storage"], output["archive_path"])
                report_name = "export-report.json"
                while report_name in {o["archive_path"] for o in outputs}:
                    report_name = "_" + report_name
                bundle.writestr(report_name, json.dumps(report, ensure_ascii=False, indent=2))
        atomic_json(
            directory / (identity + ".json"),
            {
                "dependencies": dependencies,
                "created_at": time.time(),
                "filename": slug(store.settings()["title"]) + ".zip",
                "outputs": outputs,
                "has_zip": has_zip,
            },
        )
        return {
            "id": identity,
            "files": [o["archive_path"] for o in outputs],
            "tracks": report,
            "has_zip": has_zip,
            "downloads": [
                {"index": o["index"], "name": o["name"], "path": o["archive_path"]} for o in outputs
            ],
        }
    except BaseException:
        archive.unlink(missing_ok=True)
        (directory / (identity + ".json")).unlink(missing_ok=True)
        shutil.rmtree(root, ignore_errors=True)
        raise


def verify_dependency(store, dependency):
    snapshot = store.input_snapshot(dependency["track_id"], dependency["version"], dependency["result_id"])
    metadata = store.run_metadata(snapshot["result_id"])
    if (
        cue_digest([Cue.model_validate(c) for c in snapshot["cues"]]) != dependency["digest"]
        or metadata.get("status") != dependency["status"]
    ):
        raise ValueError("匯出來源已更新，請重新匯出")


def delivery_metadata(store, identity):
    if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
        raise ValueError("Invalid delivery ID")
    directory = store.path / "exports" / ".downloads"
    return directory, json.loads((directory / (identity + ".json")).read_text())


def delivery_download(store, identity):
    directory, metadata = delivery_metadata(store, identity)
    for dependency in metadata["dependencies"]:
        verify_dependency(store, dependency)
    if not metadata.get("has_zip", True):
        raise FileNotFoundError("Single-file delivery has no archive")
    return directory / (identity + ".zip"), metadata["filename"]


def delivery_file(store, identity, index):
    directory, metadata = delivery_metadata(store, identity)
    outputs = metadata.get("outputs", [])
    if index < 0 or index >= len(outputs):
        raise FileNotFoundError("Export file does not exist")
    output = outputs[index]
    verify_dependency(store, output["dependency"])
    root = (directory / identity).resolve()
    path = (root / output["storage"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise FileNotFoundError("Export file does not exist")
    return path, output["name"]
