"""Retrieve recurring literal mentions without inferring speakers or canonical names."""

import re
from collections import defaultdict


def mention_groups(cues, facts=()):
    candidates = set()
    for cue in cues:
        candidates.update(re.findall(r"[ァ-ヺーA-Za-z一-龯]{2,}(?=ちゃん|くん|さん|様|先輩)", cue.ja))
    for fact in facts:
        if fact.get('kind') == 'entity':
            candidates.update(re.findall(r"[ァ-ヺー]{2,}", fact['quote']))
    groups = defaultdict(list)
    for token in sorted(candidates):
        for cue in cues:
            if token in cue.ja:
                groups[token].append(cue.id)
    return {token: ids for token, ids in groups.items() if len(ids) > 1}


def cross_context(cues, batch, context, groups, limit=6):
    """Read distant mentions in their original bilingual context, never replace by string."""
    indexed = {c.id: c for c in cues}
    excluded = {c.id for c in context}
    remote = []
    for token, ids in groups.items():
        if not any(token in c.ja for c in batch):
            continue
        # Prefer distant occurrences, so nearby windows do not monopolize the context.
        ordered = sorted((indexed[i] for i in ids if i not in excluded),
                         key=lambda c: min(abs(c.ordinal-b.ordinal) for b in batch), reverse=True)
        for cue in ordered:
            if cue not in remote and len(remote) < limit:
                remote.append(cue)
    return remote
