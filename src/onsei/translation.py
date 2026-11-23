from __future__ import annotations

import json
import re
from collections import defaultdict

import httpx
from opencc import OpenCC

from onsei.preferences import translation_preferences, converter_for, pin_translation_settings
from onsei.terminology import enabled_terms
from onsei.domain import Cue
from onsei.models import TRANSLATION_MODELS, REGISTRY
from onsei.storage import Store
from onsei.translation_contract import CONTEXT_CUES, polluted, split_point
from onsei.context import document_batches, translation_options, previous_translations
from onsei.translation_names import matching_names, name_terms
from onsei.util import atomic_json, digest

from onsei.translation_adapters import INSTRUCTION, parse_reply

from onsei.inference import Llama
from onsei.translation_config import model_config, model_prompt_version
from onsei.translation_diagnostics import failure_event, failure_reason


def payload(cues: list[Cue], targets: list[Cue], settings: dict, track_name: str = "") -> dict:
    indexes = [c.ordinal for c in targets]
    lo, hi = min(indexes), max(indexes)
    return {
        "instruction": INSTRUCTION,
        "track": track_name,
        **translation_preferences(settings),
        "notes": settings.get("notes", ""),
        "glossary": enabled_terms(settings.get("glossary_entries", settings.get("glossary", {}))),
        "glossary_entries": settings.get("glossary_entries", settings.get("glossary", {})),
        "automatic_names": matching_names(settings.get("automatic_names", []), [c.ja for c in targets]),
        "before": [c.ja for c in cues if lo - CONTEXT_CUES <= c.ordinal < lo],
        "after": [c.ja for c in cues if hi < c.ordinal <= hi + CONTEXT_CUES],
        "lines": [{"id": c.id, "text": c.ja, "start": c.start_ms, "end": c.end_ms} for c in targets],
    }


def validate_content(translations: dict[str, str], data: dict) -> dict[str, list[str]]:
    """Semantic heuristics are review hints, never proof or batch rejection."""
    flags = {row["id"]: [] for row in data["lines"]}
    repeated = defaultdict(set)
    converter = OpenCC("s2t")
    for row in data["lines"]:
        source, text = row["text"], translations[row["id"]]
        zh = converter.convert(text)
        if len(text) >= 10:
            repeated[text].add(source)
        terms = [(ja, converter.convert(target)) for ja, target in data["glossary"].items() if ja in source]
        if any(target not in zh for _, target in terms):
            flags[row["id"]].append("translation_glossary_question")
        if data.get("automatic_names"):
            expected_names = name_terms(data, source)
            # This is a hint, not a rejection: dialogue may naturally omit a
            # repeated name. Do not rewrite text or add another generation pass.
            if any(converter.convert(target) not in zh for target in expected_names.values()):
                flags[row["id"]].append("translation_name_question")
        # A name suffix cannot establish who is senior to whom. Preserve the
        # dialogue policy in the prompt, without a separate honorific classifier
        # or a regex veto on contextually valid review suggestions.
        if re.search(r"[ぁ-ゖァ-ヺ]{2,}", text):
            flags[row["id"]].append("translation_residual_japanese")
    for row in data["lines"]:
        if len(repeated[translations[row["id"]]]) >= 3:
            flags[row["id"]].append("translation_repetition_question")
    return {key: list(dict.fromkeys(value)) for key, value in flags.items()}


def translate(store: Store, track_id: str, model: str, selected: list[str] | None, progress, *, engine=None):
    if model not in TRANSLATION_MODELS:
        raise ValueError("Unsupported translation model")
    identity = store.prepare_translation(track_id, model)
    cues = store.cues(track_id)
    if not cues:
        raise ValueError("No Japanese transcript")
    raw_settings = store.settings()
    settings = {**raw_settings, **pin_translation_settings(raw_settings)}
    track_name = store.track(track_id).name
    directory = store.path / "cache" / track_id / "translation"
    directory.mkdir(parents=True, exist_ok=True)
    selected = set(selected or [])
    selected_ordinals = {c.ordinal for c in cues if c.id in selected}
    if selected and len(selected_ordinals) != len(selected):
        raise ValueError("Selected cues are not in the active transcript")
    converter = converter_for(settings)
    preferences = translation_preferences(settings)
    config = model_config(model, settings)
    document = [{"id": str(c.ordinal + 1), "text": c.ja} for c in cues]
    document_key = digest(document)
    batches = document_batches(cues)

    def needs_update(c):
        previous = c.provenance.get("translation", {})
        return c.zh_override is None and (
            c.translation_status in ("pending", "stale", "failed")
            or not c.zh
            or polluted(c.zh_auto)
            or previous.get("model") != model
            or previous.get("revision") != REGISTRY[model][1]
            or previous.get("prompt_version") != model_prompt_version(model)
            or previous.get("config") != config
            or previous.get("document_key") != document_key
            or translation_preferences(previous) != preferences
        )

    targets = []
    for batch in batches:
        affected = any(c.id in selected for c in batch)
        if (
            (affected and any(c.zh_override is None for c in batch))
            if selected
            else any(map(needs_update, batch))
        ):
            targets.append(batch if selected else [c for c in batch if needs_update(c)])
    if not targets:
        return
    # Hide old exports before generating replacements, including model/prompt upgrades.
    for batch in targets:
        writable = [c for c in batch if c.zh_override is None]
        for c in writable:
            c.translation_status = "stale"
        store.save_generated_batch(writable)
    nonce = store.translation_nonce(track_id, model)
    pending = sum(len(batch) for batch in targets)
    total = pending if selected else len(cues)
    completed = total - pending
    failed_cues = []
    if completed:
        progress(
            {
                "message": f"沿用已完成 {completed}/{total} 句",
                "stage": "translation",
                "fraction": completed / total,
            }
        )
    log = directory / "llama.log"
    if engine is None:
        progress(
            {
                "log_source": str(log),
                "log_start": log.stat().st_size if log.exists() else 0,
                "message": f"載入 {model} 翻譯模型",
                "stage": "translation",
            }
        )
    else:
        progress({"message": "檢查翻譯批次與快取", "stage": "translation"})
    from contextlib import nullcontext

    with (
        nullcontext(engine)
        if engine is not None
        else Llama(model, directory, translation_options=translation_options(settings, model))
    ) as engine:
        from onsei.translation_names import build_name_memory

        base = getattr(store, "store", store)
        metadata = base.run_metadata(identity)
        names = (
            build_name_memory(
                store,
                track_id,
                cues,
                settings,
                engine,
                model,
                progress,
                pinned=metadata.get("automatic_names"),
            )
            if translation_options(settings, model)["names"]
            else []
        )
        settings = {**settings, "automatic_names": names}
        base.update_run(identity, automatic_names=names)
        accepted = {
            c.id: c.zh
            for c in cues
            if c.zh
            and (
                c.zh_override is not None
                or (
                    c.translation_status == "translated"
                    and c.provenance.get("translation", {}).get("model") == model
                )
            )
        }

        def record(event, **details):
            if callable(callback := getattr(engine, "record", None)):
                callback(event, **details)

        def execution_metadata():
            callback = getattr(engine, "execution_metadata", None)
            return callback() if callable(callback) else None

        def process(batch, repetition_recovery=False):
            nonlocal completed
            data = payload(cues, batch, settings, track_name)
            if repetition_recovery:
                data["repetition_recovery"] = True
            if document is not None:
                data["document"] = document
                if translation_options(settings, model)["history"]:
                    data["document_history"] = previous_translations(cues, batch, accepted)
                for row, cue in zip(data["lines"], batch):
                    row["ordinal"] = cue.ordinal + 1
            key = digest(
                {
                    "payload": data,
                    "model": REGISTRY[model][:2],
                    "config": config,
                    "prompt": model_prompt_version(model),
                    **({"generation": nonce} if nonce else {}),
                }
            )
            cache = directory / f"{key}.json"
            ids = [c.id for c in batch]
            translations = None
            execution = None
            if cache.exists():
                try:
                    cached = json.loads(cache.read_text())
                    translations = parse_reply(json.dumps(cached["lines"], ensure_ascii=False), ids)
                    validate_content(translations, data)
                    execution = cached.get("execution")
                    record("cache_hits")
                    record("cached_lines", lines=len(batch))
                except (ValueError, KeyError, TypeError):
                    translations = None
                    execution = None
                    cache.replace(cache.with_suffix(".invalid.json"))
                    progress({"message": "快取譯文格式異常，重新翻譯", "stage": "translation"})
            if translations is None:
                error = None
                for attempt in range(2):
                    try:
                        if attempt or repetition_recovery:
                            record("retries")
                        reply = engine.generate(
                            {
                                **data,
                                "retry": attempt > 0 or repetition_recovery,
                                "retry_error": str(error or ("輸出異常重複" if repetition_recovery else "")),
                                "repetition_recovery": repetition_recovery
                                or (error is not None and failure_reason(error)[0] == "repetition"),
                                "recovery_attempt": attempt,
                            }
                        )
                        (directory / f"{key}-{attempt}.reply.txt").write_text(reply, encoding="utf-8")
                        translations = parse_reply(reply, ids)
                        validate_content(translations, data)
                        record("accepted_requests")
                        execution = execution_metadata()
                        atomic_json(
                            cache,
                            {
                                "lines": [{"id": i, "text": t} for i, t in translations.items()],
                                "model": model,
                                "revision": REGISTRY[model][1],
                                "input": data,
                                "execution": execution,
                            },
                        )
                        break
                    except (ValueError, httpx.HTTPError) as e:
                        translations = None
                        error = e
                        code, details = failure_reason(e)
                        repetition_recovery = repetition_recovery or code == "repetition"
                        index = details.get("row_index", 0) - 1
                        isolate = code == "repetition" and len(batch) > 1 and 0 <= index < len(batch)
                        skip = repetition_recovery and len(batch) == 1 and attempt == 1
                        split = len(batch) > 1 and (
                            code in {"context_limit", "repetition", "output_limit", "incomplete_output"}
                            or attempt == 1
                        )
                        callback = getattr(engine, "reject", None)
                        recovered = callback(e) if callable(callback) and code != "context_limit" else False
                        diagnostic = failure_event(
                            e,
                            batch[0].ordinal + 1,
                            batch[-1].ordinal + 1,
                            attempt + 1,
                            split=split,
                            recovered=recovered,
                            request=execution_metadata(),
                            ordinals=[c.ordinal + 1 for c in batch],
                            isolate=isolate,
                            skip=skip,
                        )
                        progress(diagnostic)
                        if isolate:
                            record("isolations")
                            if index:
                                process(batch[:index])
                            process(batch[index : index + 1], repetition_recovery=True)
                            if index + 1 < len(batch):
                                process(batch[index + 1 :])
                            return
                        if skip:
                            c = batch[0]
                            if c.zh_override is None:
                                c.translation_status = "failed"
                                store.save_generated(c, c.revision)
                            failed_cues.append(c.ordinal + 1)
                            record("failed_lines")
                            completed += 1
                            progress(
                                {
                                    "stage": "translation",
                                    "fraction": completed / total,
                                    "message": f"已處理 {completed}/{total} 句；{len(failed_cues)} 句翻譯失敗",
                                }
                            )
                            return
                        if split:
                            record("splits")
                            middle = split_point(batch)
                            process(batch[:middle])
                            process(batch[middle:])
                            return
                else:
                    if len(batch) > 1:
                        record("splits")
                        middle = split_point(batch)
                        process(batch[:middle])
                        process(batch[middle:])
                        return
                    c = batch[0]
                    if c.zh_override is None:
                        c.translation_status = "failed"
                        store.save_generated(c, c.revision)
                    raise RuntimeError(f"{diagnostic['message']}（台詞 ID：{c.id}）") from error
            semantic_flags = validate_content(translations, data)
            history = data.get("document_history", [])
            document_context = (execution or {}).get("document_context")
            if document_context is not None:
                used = set(document_context.get("history_ids", []))
                history = [row for row in history if row["id"] in used]
            writable = [c for c in batch if c.zh_override is None]
            for c in writable:
                c.zh_auto = converter.convert(translations[c.id])
                c.translation_status = "translated"
                c.provenance.pop("review", None)
                c.review_flags = [
                    flag for flag in c.review_flags if not flag.startswith("translation_")
                ] + semantic_flags[c.id]
                c.provenance["translation"] = {
                    "model": model,
                    **preferences,
                    "revision": REGISTRY[model][1],
                    "cache_key": key,
                    "automatic_names": data["automatic_names"],
                    "prompt_version": model_prompt_version(model),
                    "config": config,
                    **({"document_key": document_key} if document is not None else {}),
                    **({"document_history": history} if history else {}),
                    "execution": execution,
                    "context_ids": [
                        n.id
                        for n in cues
                        if document is not None
                        or batch[0].ordinal - CONTEXT_CUES <= n.ordinal <= batch[-1].ordinal + CONTEXT_CUES
                    ],
                }
            saved = store.save_generated_batch(writable)
            for c in writable:
                if c.id not in saved:
                    accepted.pop(c.id, None)
                    progress({"message": f"{c.id} 已被修改，略過舊推論結果"})
                else:
                    accepted[c.id] = c.zh
                    record("saved_lines", lines=1)
            completed += len(batch)
            progress(
                {
                    "message": f"已處理 {completed}/{total} 句（每批最多 {config['batch_size']} 句）",
                    "stage": "translation",
                    "fraction": completed / total,
                }
            )

        for batch in targets:
            process(batch)
        if not failed_cues:
            record("completed_tracks")
    if failed_cues:
        numbers = "、".join(map(str, failed_cues[:20])) + ("…" if len(failed_cues) > 20 else "")
        raise RuntimeError(
            f"其餘台詞已處理；{len(failed_cues)} 句異常重複仍未解決（第 {numbers} 句），已保留成功譯文，可恢復重試"
        )
    summary = getattr(engine, "summary", None)
    if isinstance(summary, dict):
        progress(
            {
                "message": f"翻譯完成：{summary['wall_seconds']:.1f} 秒，"
                f"{summary['saved_lines_per_minute'] or 0:.1f} 句／分鐘；"
                f"重試 {summary.get('retries', 0)} 次，拆批 {summary.get('splits', 0)} 次，"
                f"隔離 {summary.get('isolations', 0)} 句，"
                f"回退 {len(engine.fallbacks)} 次",
                "stage": "translation",
                "inference_run": engine.run_id,
            }
        )
