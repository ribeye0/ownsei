"""Full bilingual coverage with minimal, auditable patches in a separate result."""

import json
import re

from onsei.preferences import converter_for, effective_glossary, translation_preferences
from onsei.terminology import enabled_terms

from onsei.domain import Cue, subtitle_zh
from onsei.consistency import mention_groups, cross_context
from onsei.semantic_contract import normalize_review, review_grammar
from onsei.semantic_inference import structured_request
from onsei.translation_policy import HONORIFICS, REVIEW_INSTRUCTION
from onsei.translation_runs import TranslationSession
from onsei.translation_reference import relevant_reference
from onsei.translation_contract import polluted

REVIEW_VERSION = 16


def review(store, snapshot, identity, engine, progress, *, reference=None):
    source_metadata = store.run_metadata(snapshot["result_id"])
    if source_metadata.get("status") == "stale" or (
        source_metadata.get("stage") == "review" and source_metadata.get("status") != "completed"
    ):
        raise ValueError("審校來源待更新或尚未完成，請選擇完整有效的中文結果")
    parent = [Cue.model_validate(c) for c in snapshot["cues"]]
    if any(not c.zh or c.translation_status in ("pending", "stale", "failed") or
           (c.zh_override is None and polluted(c.zh_auto)) for c in parent):
        raise ValueError("請先完成有效初譯，再執行全文審校")
    session = TranslationSession(store, snapshot, identity, include_translation=True)
    targets = {c.id: c for c in session.cues(snapshot["track"]["id"])}
    metadata = store.run_metadata(identity)
    if metadata.get("coverage") and metadata.get("policy_version") != REVIEW_VERSION:
        raise ValueError("審校政策已更新，請另建審校稿")
    done = set(metadata.get("coverage", []))
    patches = metadata.get("patches", [])
    issues = metadata.get("issues", [])
    groups = mention_groups(parent, reference or [])
    store.update_run(identity, consistency_groups=groups)
    converter = converter_for(snapshot["settings"])
    directory = store.path / "cache" / snapshot["track"]["id"] / "review"
    directory.mkdir(parents=True, exist_ok=True)
    store.update_run(identity, status="running", policy_version=REVIEW_VERSION, reviewer=engine.model)

    def window(batch):
        if not store.snapshot_valid(snapshot, include_translation=True):
            raise ValueError("審校來源已更新或刪除，已完成審校稿保留")
        batch = [c for c in batch if c.id not in done]
        if not batch:
            return
        lo, hi = batch[0].ordinal, batch[-1].ordinal
        context = [c for c in parent if lo - 6 <= c.ordinal <= hi + 6]
        extra = cross_context(parent, batch, context, groups)
        relevant = relevant_reference(reference or [], batch)
        for fact in relevant:
            for stable_id in fact.get("evidence_ids", []):
                cue = next((c for c in parent if c.id == stable_id), None)
                if cue and cue not in context and cue not in extra and len(extra) < 6:
                    extra.append(cue)
        evidence = context + extra
        mapping = {str(c.ordinal + 1): c.id for c in evidence}
        reverse = {value: key for key, value in mapping.items()}
        ids = [str(c.ordinal + 1) for c in batch]
        data = {"settings": {k: snapshot["settings"].get(k) for k in ("notes", "glossary")},
                "recurring_mentions": {token: [reverse[i] for i in ids if i in reverse]
                                       for token, ids in groups.items()
                                       if any(token in c.ja for c in batch)},
                "reference": [{**f, "evidence_ids": [reverse[i] for i in f["evidence_ids"]]}
                              for f in relevant if all(i in reverse for i in f["evidence_ids"])], "target_ids": ids,
                "dialogue": [{"id": str(c.ordinal + 1), "ja": c.ja, "zh": c.zh,
                              "human_locked": c.zh_override is not None,
                              "hints": c.review_flags} for c in evidence]}
        data["settings"]["glossary"] = effective_glossary(snapshot["settings"], [c.ja for c in batch])
        prompt = "你是日中字幕校對員，採取保守校對：只修正原文及上下文能證明的翻譯錯誤，無錯誤就保留。\n" + HONORIFICS + REVIEW_INSTRUCTION + (
            '\n逐句依序輸出審校JSON陣列。每項先寫id、source（日文原样），再選action：'
            'keep（譯文已可用，不輸出text）、change（確定需要修改，先附kind、reason、evidence_ids，再輸出符合理由的text）、'
            'uncertain（無法確定的具體問題，只附kind、reason、evidence_ids）。'
            '合理同義表達保留；不要為了填欄位而改寫或製造疑點。只處理target_ids。\n'
        ) + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if translation_preferences(snapshot["settings"])["target_language"] == "zh-Hans":
            prompt = prompt.replace("台灣繁中", "简体中文")
        lines = [{"id":str(c.ordinal+1),"ja":c.ja} for c in batch]
        try:
            result = structured_request(engine, prompt, review_grammar(lines, list(mapping)),
                                        lambda raw: normalize_review(raw, lines, list(mapping)), directory, progress,
                                        stage="review", lines=len(batch))
        except ValueError:
            if len(batch) == 1:
                raise
            engine.record("splits")
            mid = len(batch) // 2
            window(batch[:mid])
            window(batch[mid:])
            return
        if not store.snapshot_valid(snapshot, include_translation=True):
            raise ValueError("審校來源已更新，停止套用舊推論")
        changes = {mapping[p["id"]]: {**p, "id": mapping[p["id"]],
                                    "evidence_ids": [mapping[i] for i in p["evidence_ids"]]}
                   for p in result["patches"]}
        new_issues = [{**p, "id": mapping[p["id"]], "evidence_ids": [mapping[i] for i in p["evidence_ids"]]}
                      for p in result["issues"]]
        for original in batch:
            from onsei.translation import validate_content
            cue = targets[original.id]
            change = changes.get(cue.id)
            before = cue.zh
            details = {"reviewed": True, "review_version": REVIEW_VERSION, "reviewer": engine.model}
            content = {"lines": [{"id": cue.id, "text": cue.ja}], "glossary": enabled_terms(snapshot["settings"].get("glossary_entries", snapshot["settings"].get("glossary", {})))}
            if change:
                after = subtitle_zh(converter.convert(change["text"]))
                if not after.strip():
                    raise ValueError("審校譯文不可只有標點")
                change.update(before=before, text=after, applied=False)
                after_flags = set(validate_content({cue.id: after}, content)[cue.id])
                unresolved = any(word in change["reason"] for word in ("需確認", "待確認", "無法確認", "不能確定", "指代不明"))
                if cue.zh_override is not None or change["kind"] in ("asr", "uncertain") or unresolved or after_flags:
                    explanation = ("人工稿受保護" if cue.zh_override is not None else
                                   "建議仍含語意疑點：" + "、".join(sorted(after_flags)) if after_flags else "判斷尚待確認")
                    new_issues.append({**change, "reason": explanation + "；保留原稿。建議：" + change["reason"]})
                elif re.sub(r"\W", "", after) != re.sub(r"\W", "", before):
                    cue.zh_auto = after
                    change["applied"] = True
                    details.update(change)
                if change["applied"] or new_issues and new_issues[-1].get("id") == cue.id:
                    patches.append(change)
            current_flags = validate_content({cue.id: cue.zh}, content)
            cue.review_flags = [f for f in cue.review_flags if not f.startswith("translation_")] + current_flags[cue.id]
            cue.provenance["review"] = details
            local_issues = [i for i in new_issues if i["id"] == cue.id]
            if local_issues:
                cue.review_flags = [f for f in cue.review_flags if f != "translation_review_issue"] + ["translation_review_issue"]
            if not session.save_generated(cue, cue.revision):
                if change:
                    change["applied"] = False
                issues.append({"id": cue.id, "kind": "uncertain", "reason": "審校期間中文已被修改，未覆寫", "evidence_ids": [cue.id]})
                store.update_run(identity, status="partial", coverage=sorted(done), patches=patches, issues=issues)
                raise ValueError("審校稿已被修改，請保留人工修改並重新審校")
            done.add(cue.id)
        issues.extend(new_issues)
        store.update_run(identity, coverage=sorted(done), patches=patches, issues=issues)
        progress({"stage": "review", "message": f"審校 {len(done)}/{len(parent)} 句；"
                  f"修改 {sum(p.get('applied', False) for p in patches)} 句，疑點 {len(issues)} 項",
                  "fraction": len(done) / len(parent)})

    try:
        size = len(parent) if len(parent) <= 24 else 16
        for start in range(0, len(parent), size):
            window(parent[start:start + size])
        store.update_run(identity, status="completed", coverage=sorted(done), patches=patches, issues=issues)
    except BaseException as error:
        stale = not store.snapshot_valid(snapshot, include_translation=True)
        store.update_run(identity, status="stale" if stale else "partial", coverage=sorted(done), patches=patches, issues=issues,
                         execution_status="cancelled" if isinstance(error, (InterruptedError, KeyboardInterrupt)) else "failed", error=str(error))
        raise
    return identity
