"""Bounded document context for the optional NativeLong subtitle adapter."""

NATIVE_LONG = "index-nativelong-9b"
CONTEXT_SIZES = (8192, 16384, 32768, 65536, 98304, 131072, 262144)
DOCUMENT_CONFIG = {
    "context": 65536,
    "output_tokens": 6144,
    "batch_size": 64,
    "max_batch_source_chars": 1600,
    "context_mode": "track-or-contiguous-window",
    "document_policy": 2,
    "history_cues": 128,
    "history_chars": 6000,
}


def drop_retired_format(value):
    """Accept settings from saved jobs and older browser tabs."""
    if isinstance(value, dict) and "compact_rows" in value:
        return {key: item for key, item in value.items() if key != "compact_rows"}
    return value


def translation_options(settings=None, model=NATIVE_LONG):
    value = drop_retired_format((settings or {}).get("translation_options", {}))
    if not isinstance(value, dict):
        raise ValueError("Invalid translation settings")
    result = {
        "context": DOCUMENT_CONFIG["context"] if model == NATIVE_LONG else 8192,
        "names": model != NATIVE_LONG,
        "history": model == NATIVE_LONG,
        **value,
    }
    if (
        set(result) != {"context", "names", "history"}
        or type(result["context"]) is not int
        or result["context"] not in CONTEXT_SIZES
        or type(result["names"]) is not bool
        or type(result["history"]) is not bool
    ):
        raise ValueError("Invalid translation settings")
    return result


def previous_translations(cues, targets, accepted, *, limit=None):
    """Contiguous accepted Chinese immediately before targets; never future or failed rows."""
    result, size = [], 0
    first = min(c.ordinal for c in targets)
    by_ordinal = {c.ordinal: c for c in cues}
    for ordinal in range(first - 1, max(-1, first - DOCUMENT_CONFIG["history_cues"] - 1), -1):
        cue = by_ordinal.get(ordinal)
        if cue is None or cue.id not in accepted:
            break
        text = accepted[cue.id]
        size += len(cue.ja) + len(text)
        if size > (DOCUMENT_CONFIG["history_chars"] if limit is None else limit):
            break
        result.append({"id": str(cue.ordinal + 1), "ja": cue.ja, "zh": text})
    return list(reversed(result))


def document_batches(cues):
    batches, batch, characters = [], [], 0
    for cue in cues:
        if batch and (
            len(batch) >= DOCUMENT_CONFIG["batch_size"]
            or characters + len(cue.ja) > DOCUMENT_CONFIG["max_batch_source_chars"]
        ):
            batches.append(batch)
            batch, characters = [], 0
        batch.append(cue)
        characters += len(cue.ja)
    if batch:
        batches.append(batch)
    return batches


def document_window(document, target_ids, size):
    """Keep all targets and their nearest contiguous source context in order."""
    positions = [i for i, row in enumerate(document) if row["id"] in target_ids]
    if len(positions) != len(target_ids):
        raise ValueError("Document context is missing target IDs")
    lo, hi = min(positions), max(positions) + 1
    size = min(len(document), max(size, hi - lo))
    start = max(0, min(lo - (size - (hi - lo)) // 2, len(document) - size))
    return document[start : start + size]


def fit_context(data, target_ids, capacity, output_budget, render, initial):
    """Fit actual tokenized prompts without dropping targets or changing IDs."""
    from onsei.util import digest

    document = data.get("document", [])
    effective = document
    history = list(data.get("document_history", []))
    prompt, rendered, count = initial
    while history and count + output_budget + 64 > capacity:
        history = history[1:]
        data = {**data, "document_history": history}
        prompt, rendered, count = render(data)
    if count + output_budget + 64 > capacity and document:
        minimum = len(document_window(document, target_ids, 0))
        low, high, best = minimum, len(document) - 1, None
        while low <= high:
            size = (low + high) // 2
            window = document_window(document, target_ids, size)
            candidate = render({**data, "document": window})
            if candidate[2] + output_budget + 64 <= capacity:
                best = (*candidate, window)
                low = size + 1
            else:
                high = size - 1
        if best:
            prompt, rendered, count, effective = best
    metadata = {
        "scope": "track" if len(effective) == len(document) else "window",
        "total_lines": len(document),
        "included_lines": len(effective),
        "first_id": effective[0]["id"] if effective else None,
        "last_id": effective[-1]["id"] if effective else None,
        "source_sha256": digest(document),
        "history_lines": len(history),
        "history_ids": [r["id"] for r in history],
    }
    return data, prompt, rendered, count, metadata
