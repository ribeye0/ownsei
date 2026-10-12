"""Translation preferences independent of interface language, pinned per task."""

import re
from opencc import OpenCC
from onsei.translation_policy import STYLE_INSTRUCTION, SIMPLIFIED_STYLE_INSTRUCTION
from onsei.terminology import normalize_entries, project_terms

TARGETS = {"zh-Hant": "繁體中文", "zh-Hans": "简体中文"}
DEFAULT_STYLES = {"zh-Hant": STYLE_INSTRUCTION, "zh-Hans": SIMPLIFIED_STYLE_INSTRUCTION}
MAX_STYLE_LENGTH = 2000


def validate_style(style):
    if not isinstance(style, str) or not style.strip() or len(style) > MAX_STYLE_LENGTH:
        raise ValueError("翻譯風格指引須為 1–2000 字")
    return style.strip()


def style_variants(settings):
    variants = dict(DEFAULT_STYLES)
    custom = settings.get("translation_styles", {})
    if not isinstance(custom, dict) or set(custom) - set(TARGETS):
        raise ValueError("翻譯風格只接受繁體與簡體版本")
    variants.update({target: validate_style(style) for target, style in custom.items()})
    legacy = settings.get("translation_style")
    target = settings.get("target_language") or "zh-Hant"
    if not isinstance(target, str) or target not in TARGETS:
        raise ValueError("不支援的翻譯目標語言")
    if legacy is not None and target not in custom:
        # Old factory prompts become the dedicated factory prompt; custom prose
        # stays attached to its original target without being rewritten.
        variants[target] = DEFAULT_STYLES[target] if legacy == STYLE_INSTRUCTION else validate_style(legacy)
    return variants


def translation_preferences(settings):
    target = settings.get("target_language") or "zh-Hant"
    if not isinstance(target, str) or target not in TARGETS:
        raise ValueError("不支援的翻譯目標語言")
    style = (validate_style(settings["translation_style"]) if "global_glossary" in settings and "translation_style" in settings
             else style_variants(settings)[target])
    result = {"target_language": target, "translation_style": style}
    if "global_glossary" in settings:
        result["global_glossary"] = normalize_entries(settings["global_glossary"])
    return result


def updated_preferences(settings, changes):
    """Keep both language variants, selecting the requested target explicitly."""
    variants = style_variants(settings)
    if "translation_styles" in changes:
        supplied = changes["translation_styles"]
        if not isinstance(supplied, dict) or set(supplied) - set(TARGETS):
            raise ValueError("翻譯風格只接受繁體與簡體版本")
        variants.update({key: validate_style(value) for key, value in supplied.items()})
    target = changes.get("target_language", settings.get("target_language")) or "zh-Hant"
    if not isinstance(target, str) or target not in TARGETS:
        raise ValueError("不支援的翻譯目標語言")
    if "translation_style" in changes:
        variants[target] = validate_style(changes["translation_style"])
    return {"target_language": target, "translation_styles": variants, "translation_style": variants[target]}


def pin_translation_settings(settings, global_entries=None):
    preferences = translation_preferences(settings)
    if "global_glossary" not in preferences:
        if global_entries is None:
            from onsei.global_preferences import load_global
            global_entries = load_global()["glossary_entries"]
        preferences["global_glossary"] = normalize_entries(global_entries)
    return preferences


def converter_for(settings):
    return OpenCC("t2s" if translation_preferences(settings)["target_language"] == "zh-Hans" else "s2t")


def effective_style(settings):
    return translation_preferences(settings)["translation_style"]


def kana_key(text):
    """Compare full-width kana spellings without editing the ASR source text."""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def effective_glossary(settings, sources):
    from onsei.translation_policy import DEFAULT_GLOSSARY
    global_entries = normalize_entries(settings.get("global_glossary", DEFAULT_GLOSSARY))
    common = {e["ja"]: e["zh"] if e["enabled"] else None for e in global_entries}
    local = project_terms(settings)
    def grouped(terms):
        groups = {}
        for word, value in terms.items():
            groups.setdefault(kana_key(word), {})[word] = value
        return groups

    # ASR can spell the same term in either kana script. Project overrides and
    # masks apply to that spelling family; distinct explicit spellings within
    # the same scope still win exactly, and ambiguous aliases are not guessed.
    resolved = {**grouped(common), **grouped(local)}
    # Include disabled/overridden long forms when matching, so masking a word
    # does not accidentally activate a shorter global term inside that word.
    pattern = "|".join(re.escape(word) for word in sorted(resolved, key=len, reverse=True))
    terms = {}
    for source in sources:
        for match in re.finditer(pattern, kana_key(source)) if pattern else []:
            word = source[match.start():match.end()]
            choices = resolved[match.group()]
            values = set(choices.values())
            if word in choices:
                value = choices[word]
            elif len(values) == 1:
                value = next(iter(values))
            else:
                continue
            if value is not None:
                terms[word] = value
    terms.update({ja: zh for ja, zh in local.items() if zh is not None})
    converter = converter_for(settings)
    return {word: converter.convert(value) for word, value in terms.items()}
