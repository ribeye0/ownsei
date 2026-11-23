"""Store shared model/context provenance once per translation result or queued snapshot."""

import hashlib
import json


def encode_batch(metadata):
    raw = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest(), raw


def intern_batch(db, result, metadata):
    key, raw = encode_batch(metadata)
    db.execute("INSERT OR IGNORE INTO translation_batches VALUES (?,?,?)", (result, key, raw))
    return key


def store_payload(db, result, payload, batch_ref=None):
    value = dict(payload)
    metadata = value.get("translation")
    if metadata:
        value.pop("translation")
        value["batch_ref"] = batch_ref or intern_batch(db, result, metadata)
    return json.dumps(value, ensure_ascii=False)


def batch_records(db, result):
    return {
        key: json.loads(raw)
        for key, raw in db.execute("SELECT id,payload FROM translation_batches WHERE result_id=?", (result,))
    }


def expand_payload(raw, batches):
    value = json.loads(raw)
    if key := value.pop("batch_ref", None):
        value["translation"] = batches[key]
    return value


def translation_values(db, result, *, diagnostics=True):
    batches = batch_records(db, result)
    if not diagnostics:
        # Timed subtitles only need the script tag. Keep full metadata for JSONL.
        batches = {
            key: {k: v for k, v in meta.items() if k == "target_language"} for key, meta in batches.items()
        }
    return {
        cue_id: expand_payload(raw, batches)
        for cue_id, raw in db.execute(
            "SELECT cue_id,payload FROM translation_cues WHERE result_id=?", (result,)
        )
    }


def compact_snapshot(snapshot):
    if "translation_batches" in snapshot:
        return snapshot
    batches, cues = {}, []
    for cue in snapshot["cues"]:
        provenance = cue.get("provenance", {})
        if metadata := provenance.get("translation"):
            key, _ = encode_batch(metadata)
            batches.setdefault(key, metadata)
            provenance = dict(provenance)
            del provenance["translation"]
            cue = {**cue, "provenance": provenance, "batch_ref": key}
        cues.append(cue)
    return {**snapshot, "cues": cues, "translation_batches": batches} if batches else snapshot


def expand_snapshot(snapshot):
    if "translation_batches" not in snapshot:
        return snapshot
    snapshot = dict(snapshot)
    batches = snapshot.pop("translation_batches")
    cues = []
    for cue in snapshot["cues"]:
        if "batch_ref" in cue:
            cue = dict(cue)
            metadata = batches[cue.pop("batch_ref")]
            cue["provenance"] = {**cue["provenance"], "translation": metadata}
        cues.append(cue)
    return {**snapshot, "cues": cues}
