"""Editable terminology with explicit disabled overrides and legacy JSON support."""


def normalize_entries(value):
    if isinstance(value, dict):
        value = [{"ja": ja, "zh": zh, "enabled": True} for ja, zh in value.items()]
    if not isinstance(value, list):
        raise ValueError("術語表須為 JSON 陣列或日中對照物件")
    entries, seen = [], set()
    for row in value:
        if not isinstance(row, dict) or set(row) - {"ja", "zh", "enabled"}:
            raise ValueError("每條術語只接受 ja、zh、enabled 欄位")
        ja, zh, enabled = row.get("ja"), row.get("zh"), row.get("enabled", True)
        if not isinstance(ja, str) or not isinstance(zh, str) or not ja.strip() or not zh.strip():
            raise ValueError("術語的日文與中文不可空白")
        if not isinstance(enabled, bool):
            raise ValueError("術語 enabled 須為 true 或 false")
        ja, zh = ja.strip(), zh.strip()
        if ja in seen:
            raise ValueError("術語日文重複：" + ja)
        seen.add(ja)
        entries.append({"ja": ja, "zh": zh, "enabled": enabled})
    return entries


def project_terms(settings):
    entries = normalize_entries(settings.get("glossary_entries", settings.get("glossary", {})))
    return {e["ja"]: e["zh"] if e["enabled"] else None for e in entries}


def enabled_terms(entries):
    return {e["ja"]: e["zh"] for e in normalize_entries(entries) if e["enabled"]}
