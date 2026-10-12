"""Constrained review/reference JSON. IDs are grounded; meaning remains a model judgement."""

import json

from onsei.translation_contract import polluted
from onsei.domain import subtitle_zh


def semantic_grammar(target_ids, evidence_ids, *, reference=False):
    if not target_ids or not evidence_ids:
        raise ValueError("Structured task requires source IDs")

    def literal(value):
        return json.dumps(value, ensure_ascii=False)

    def enum(values):
        return " | ".join(literal(json.dumps(v, ensure_ascii=False)) for v in values)

    def field(name, value):
        return literal(json.dumps(name)) + ' ws ":" ws ' + value

    def obj(fields):
        return '"{" ws ' + ' ws "," ws '.join(field(k, v) for k, v in fields) + ' ws "}"'

    rules = [r'ws ::= [ \t\n\r]*', r'string ::= "\"" char+ "\""',
             r'char ::= [^"\\{}<>\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4})',
             'id ::= ' + enum(target_ids), 'evidence-id ::= ' + enum(evidence_ids),
             'evidence ::= "[" ws evidence-id (ws "," ws evidence-id){0,2} ws "]"']
    if reference:
        rules = [r if not r.startswith('evidence ::=') else 'evidence ::= "[" ws evidence-id (ws "," ws evidence-id){0,2} ws "]"' for r in rules]
        rules += ['kind ::= ' + enum(["entity", "address", "scene", "event", "uncertain"]),
                  'fact ::= ' + obj([("kind", "kind"), ("text", "string"), ("evidence_ids", "evidence")]),
                  'facts ::= "[" ws (fact (ws "," ws fact){0,5})? ws "]"',
                  'root ::= ws ' + obj([("facts", "facts")]) + ' ws']
    else:
        rules += ['kind ::= ' + enum(["meaning", "entity", "honorific", "perspective", "omission",
                                      "alignment", "style", "asr", "uncertain"]),
                  r'reason ::= "\"" char{1,120} "\""']
        # A sorted optional subset: each target ID can occur only once in either list.
        # This prevents otherwise-valid repeated patches from consuming the output budget.
        for category in ("patch", "issue"):
            choices = []
            for index, identity in enumerate(target_ids):
                fields = [("id", literal(json.dumps(identity)))]
                if category == "patch":
                    fields.append(("text", "string"))
                fields += [("kind", "kind"), ("reason", "reason"), ("evidence_ids", "evidence")]
                rules.append(f'{category}{index} ::= ' + obj(fields))
                choices.append(f'{category}{index} {category}-tail{index}')
                following = [f'{category}{j} {category}-tail{j}' for j in range(index+1, len(target_ids))]
                rules.append(f'{category}-tail{index} ::= ' + (
                    '(ws "," ws (' + ' | '.join(following) + '))?' if following else 'ws'))
            rules.append(('patches' if category == 'patch' else 'issues') + ' ::= "[" ws (' + ' | '.join(choices) + ')? ws "]"')
        rules += ['root ::= ws ' + obj([("patches", "patches"), ("issues", "issues")]) + ' ws']
    return "\n".join(rules)


def parse_review(raw, target_ids, evidence_ids):
    result = json.loads(raw)
    if not isinstance(result, dict) or set(result) != {"patches", "issues"}:
        raise ValueError("Review must contain patches and issues")
    for kind in ("patches", "issues"):
        if not isinstance(result[kind], list):
            raise ValueError("Review fields must be arrays")
        seen = set()
        previous = -1
        for row in result[kind]:
            expected = {"id", "kind", "reason", "evidence_ids"} | ({"text"} if kind == "patches" else set())
            if not isinstance(row, dict) or set(row) != expected:
                raise ValueError("Unexpected review fields")
            if not isinstance(row["id"], str) or row["id"] not in target_ids or row["id"] in seen:
                raise ValueError("Invalid/duplicate review target ID")
            index = target_ids.index(row["id"])
            if index <= previous:
                raise ValueError("Review target order differs from source")
            previous = index
            seen.add(row["id"])
            if not isinstance(row["kind"], str) or row["kind"] not in {"meaning", "entity", "honorific", "perspective", "omission", "alignment", "style", "asr", "uncertain"}:
                raise ValueError("Unknown review issue kind")
            if not isinstance(row["reason"], str) or not row["reason"].strip() or len(row["reason"]) > 120:
                raise ValueError("Review reason must be short and nonempty")
            if not isinstance(row["evidence_ids"], list) or not 1 <= len(row["evidence_ids"]) <= 3 or any(
                not isinstance(i, str) or i not in evidence_ids for i in row["evidence_ids"]
            ):
                raise ValueError("Review references unknown evidence IDs")
            if kind == "patches" and (not isinstance(row["text"], str) or not subtitle_zh(row["text"]).strip() or polluted(row["text"])):
                raise ValueError("Invalid review translation text")
    return result


def review_grammar(lines, evidence_ids):
    """An ordered review decision for every source, with sparse changes rather than rewrites."""
    def literal(value):
        return json.dumps(value, ensure_ascii=False)
    def field(key, value):
        return literal(json.dumps(key)) + ' ws ":" ws ' + value
    def obj(fields):
        return '"{" ws ' + ' ws "," ws '.join(field(k,v) for k,v in fields) + ' ws "}"'
    rules = [r'ws ::= [ \t\n\r]*', r'string ::= "\"" char+ "\""',
             r'char ::= [^"\\{}<>\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4})',
             r'reason ::= "\"" char{1,120} "\""',
             'evidence-id ::= ' + ' | '.join(literal(json.dumps(i)) for i in evidence_ids),
             'evidence ::= "[" ws evidence-id (ws "," ws evidence-id){0,2} ws "]"',
             'kind ::= ' + ' | '.join(literal(json.dumps(i)) for i in
                                     ['meaning','entity','honorific','perspective','omission','alignment','style','asr','uncertain']),
             'change-kind ::= ' + ' | '.join(literal(json.dumps(i)) for i in
                                     ['meaning','entity','honorific','perspective','omission','alignment','style']),
             'root ::= ws "[" ws ' + ' ws "," ws '.join(f'row{i}' for i in range(len(lines))) + ' ws "]" ws']
    for index,line in enumerate(lines):
        base = [('id',literal(json.dumps(line['id']))), ('source',literal(json.dumps(line['ja'],ensure_ascii=False)))]
        choices = []
        for action in ('keep','change','uncertain'):
            fields = base + [('action',literal(json.dumps(action)))]
            if action != 'keep':
                fields += [('kind','change-kind' if action == 'change' else 'kind'),('reason','reason'),('evidence_ids','evidence')]
            if action == 'change':
                fields.append(('text','string'))
            choices.append(obj(fields))
        rules.append(f'row{index} ::= ' + ' | '.join(choices))
    return '\n'.join(rules)


def normalize_review(raw, lines, evidence_ids):
    rows = json.loads(raw)
    if not isinstance(rows,list) or len(rows)!=len(lines):
        raise ValueError('Review row count differs from source')
    result={'patches':[],'issues':[]}
    for row,line in zip(rows,lines):
        if not isinstance(row,dict) or row.get('id')!=line['id'] or row.get('source')!=line['ja']:
            raise ValueError('Review source/ID mismatch')
        action=row.get('action')
        fields={'id','source','action'}
        if action=='keep':
            if set(row)!=fields:
                raise ValueError('Keep decision must not include a rewritten translation')
            continue
        if action not in ('change','uncertain'):
            raise ValueError('Unknown review action')
        fields |= {'kind','reason','evidence_ids'} | ({'text'} if action=='change' else set())
        if set(row)!=fields:
            raise ValueError('Unexpected review decision fields')
        if action=='change' and row['kind'] in ('asr','uncertain'):
            raise ValueError('Unconfirmed suggestions must use uncertain action')
        result['patches' if action=='change' else 'issues'].append({k:v for k,v in row.items() if k not in ('source','action')})
    return parse_review(json.dumps(result,ensure_ascii=False),[r['id'] for r in lines],set(evidence_ids))
