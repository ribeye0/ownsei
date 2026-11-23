"""Shared translation windows and output checks, independent of inference runtimes."""

import json
import re

BATCH_SIZE = 24
CONTEXT_CUES = 6
# A conservative output-size hint, not a tokenizer. Native token-budget checks
# remain authoritative. Keep every sub-batch inside its original dependency grid.
MAX_SOURCE_CHARS = 650


def dependency_window(ordinal: int) -> tuple[int, int]:
    start = ordinal // BATCH_SIZE * BATCH_SIZE
    return start - CONTEXT_CUES, start + BATCH_SIZE + CONTEXT_CUES


_POLLUTION = re.compile(
    # Manual overrides are outside this generated-text contract.
    r'[{}<>]|\bthink_never_used_[\w-]+\b|"[A-Za-z_][\w-]*"\s*:',
    re.IGNORECASE,
)


def polluted(text: str | None) -> bool:
    # Observed native-model failure: a JSON value fragment escaped inside text.
    # Match only the entire malformed fragment, not legitimate dialogue about
    # null values or names containing that word.
    return bool(text and (_POLLUTION.search(text) or re.fullmatch(r"\s*:\s*null\s*", text)))


def output_grammar(
    ids: list[str], spans: list[tuple[int, int]] | None = None, sources: list[str] | None = None
) -> str:
    """Exact ordered JSON rows with properly escaped strings, no extra fields.

    llama.cpp's string-pattern conversion can consume unescaped quotes as string
    content, accidentally admitting extra JSON keys. Explicit escaping avoids it.
    Optional spans use the ASMR model's native ids/text/start/end contract.
    """
    if not ids or len(set(ids)) != len(ids) or (spans is not None and len(spans) != len(ids)):
        raise ValueError("Translation grammar requires unique IDs and matching spans")
    if sources is not None and len(sources) != len(ids):
        raise ValueError("Grounded grammar requires matching sources")

    def literal(text):
        return json.dumps(text, ensure_ascii=False)

    rules = [
        'root ::= ws "[" ws ' + ' ws "," ws '.join(f"row{i}" for i in range(len(ids))) + ' ws "]" ws',
        r'text ::= "\"" char+ "\""',
        r'char ::= [^"\\{}<>\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4})',
        r"ws ::= [ \t\n\r]*",
    ]
    for i, id in enumerate(ids):
        if spans is None:
            fields = f'{literal('"id"')} ws ":" ws {literal(json.dumps(id))}'
            suffix = ""
        else:
            start, end = spans[i]
            fields = f'{literal('"ids"')} ws ":" ws "[" ws "{int(id)}" ws "]"'
            suffix = f' ws "," ws {literal('"start"')} ws ":" ws "{start}" ws "," ws {literal('"end"')} ws ":" ws "{end}"'
        if sources is not None:
            fields += f' ws "," ws {literal('"source"')} ws ":" ws {literal(json.dumps(sources[i], ensure_ascii=False))}'
        rules.append(
            f'row{i} ::= "{{" ws {fields} ws "," ws {literal('"text"')} ws ":" ws text{suffix} ws "}}"'
        )
    return "\n".join(rules)


def split_point(batch):
    middle = len(batch) // 2
    candidates = range(max(1, middle - 3), min(len(batch), middle + 4))
    return max(candidates, key=lambda i: (batch[i].start_ms - batch[i - 1].end_ms, -abs(i - middle)))


def natural_batches(cues):
    # Preserve the established 24-cue dependency grid; use pauses inside it when useful.
    batches = []
    for start in range(0, len(cues), BATCH_SIZE):
        block = cues[start : start + BATCH_SIZE]
        cut = next(
            (i for i in range(12, len(block) - 5) if block[i].start_ms - block[i - 1].end_ms >= 2200), None
        )
        for section in [block[:cut], block[cut:]] if cut else [block]:
            current, characters = [], 0
            for cue in section:
                if current and characters + len(cue.ja) > MAX_SOURCE_CHARS:
                    batches.append(current)
                    current, characters = [], 0
                current.append(cue)
                characters += len(cue.ja)
            if current:
                batches.append(current)
    return batches
