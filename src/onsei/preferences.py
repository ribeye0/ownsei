"""Translation preferences independent of interface language, pinned per task."""

import re
from opencc import OpenCC
from onsei.translation_policy import STYLE_INSTRUCTION, SIMPLIFIED_STYLE_INSTRUCTION
from onsei.terminology import normalize_entries, project_terms

TARGETS = {"zh-Hant": "繁體中文", "zh-Hans": "简体中文"}
DEFAULT_STYLES = {"zh-Hant": STYLE_INSTRUCTION, "zh-Hans": SIMPLIFIED_STYLE_INSTRUCTION}
# Settings previously saved factory text verbatim. Upgrade exact matches only.
_OLD_HANT = (
    "將日語角色台詞譯成適合二次元音聲字幕的繁體中文，像角色正在對眼前的人說話，自然、順口、簡潔。"
    "依原文保留人物的禮貌、親疏與情緒變化；自稱、稱呼和語尾隨人物、對象及場景自然承接。"
    "口吃、拉長音、停頓與重複用自然中文保留表演節奏，不必逐字複製日語語序。省略主語可以維持省略。"
    "用詞保留原文的直白或委婉程度；使用者術語及備註優先，機器背景僅供參考。"
    "不增刪意思或人物關係，不把直接對話改成第三人稱旁白，不擅加可愛口吻、流行梗或裝飾符號。"
    "只翻譯目標句段，背景只供理解，每個ID恰好對應一項譯文，text只放台詞。"
    "字幕句尾不加句號或逗號，保留問號、驚嘆號與必要省略號。"
)
_OLD_HANS = (
    "将日语角色台词译成适合二次元音声字幕的简体中文，像角色正在对眼前的人说话，自然、顺口、简洁。"
    "保留人物的礼貌、亲疏与情绪变化；自称、称呼和语尾随人物、对象及场景自然承接。"
    "用自然中文保留口吃、拉长音、停顿与重复的表演节奏，不照搬日语语序；省略的主语可以继续省略。"
    "用词保留原文的直白或委婉程度，作品术语和备注优先，不刻意加入书面腔、地域网络梗或可爱口吻。"
    "不增删意思或人物关系，不把直接对话改成第三人称旁白。"
    "只翻译目标句段，背景仅供理解，每个ID对应一项译文，text只放台词。"
    "字幕句尾不加句号或逗号，保留问号、感叹号与必要省略号。"
)
_PREVIOUS_DEFAULT_HANT = (
    "將日語角色台詞譯成適合日式二次元動畫字幕的繁體中文。讓對白像角色當面說出口一樣，自然、順口。"
    "從角色對誰說話、當下是什麼心情，拿捏自稱、稱呼和句尾語氣，保留原文的禮貌與親疏。"
    "遇到口吃、拉長音、停頓或重複，用中文的節奏表現。原文省略主語，中文也說得通時就省略。"
    "\n原文說得直白，譯文就直白，委婉處也保留分寸。用詞與稱呼以使用者的術語和備註為優先。"
    "忠於原意與人物關係，直接對話維持角色口吻，不改寫成第三人稱旁白。"
    "字幕句尾不加句號或逗號，保留問號、驚嘆號與必要的省略號。"
)
_PREVIOUS_HANT = _PREVIOUS_DEFAULT_HANT.replace("自然、順口。", "自然、順口、簡潔。", 1)
_PREVIOUS_DEFAULT_HANS = (
    "将日语角色台词译成适合日式二次元动画字幕的简体中文。让对白像角色当面说出来一样，自然、顺口。"
    "根据角色在跟谁说话、当下是什么心情，把握自称、称呼和句尾语气，保留原文的礼貌与亲疏。"
    "遇到结巴、拖长音、停顿或重复，用中文的节奏表现。原文省略主语，中文也说得通时就省略。"
    "\n原文说得直白，译文就直白，委婉处也保留分寸。用词与称呼以用户的术语和备注为先。"
    "忠于原意和人物关系，直接对话保留角色口吻，不改写成第三人称旁白。"
    "字幕句尾不加句号或逗号，保留问号、感叹号和必要的省略号。"
)
_PREVIOUS_HANS = _PREVIOUS_DEFAULT_HANS.replace("自然、顺口。", "自然、顺口、简洁。", 1)
_OLD_TRADITIONAL_STYLES = (
    _PREVIOUS_DEFAULT_HANT.replace("字幕的繁體中文", "字幕的台灣繁體中文").replace("禮貌與親疏", "親疏"),
    _PREVIOUS_DEFAULT_HANT,
    _PREVIOUS_HANT,
    _PREVIOUS_HANT.replace("\n", "\n\n", 1),
    _OLD_HANT,
    _OLD_HANT.replace("音聲字幕的繁體中文", "音聲字幕的台灣繁體中文", 1),
)
_OLD_SIMPLIFIED_STYLES = (
    _PREVIOUS_DEFAULT_HANS,
    _OLD_HANS,
    _PREVIOUS_HANS,
    _PREVIOUS_HANS.replace("\n", "\n\n", 1),
)
MAX_STYLE_LENGTH = 2000
NAME_SUFFIXES = ("ちゃん", "くん", "君", "さん", "さま", "様", "先輩", "センパイ", "せんぱい", "殿")


def validate_style(style):
    if not isinstance(style, str) or not style.strip() or len(style) > MAX_STYLE_LENGTH:
        raise ValueError("翻譯風格指引須為 1–2000 字")
    return style.strip()


def style_variants(settings):
    variants = dict(DEFAULT_STYLES)
    custom = settings.get("translation_styles", {})
    if not isinstance(custom, dict) or set(custom) - set(TARGETS):
        raise ValueError("翻譯風格只接受繁體與簡體版本")
    for target, style in custom.items():
        old_styles = _OLD_TRADITIONAL_STYLES if target == "zh-Hant" else _OLD_SIMPLIFIED_STYLES
        if style not in old_styles:
            variants[target] = validate_style(style)
    legacy = settings.get("translation_style")
    target = settings.get("target_language") or "zh-Hant"
    if not isinstance(target, str) or target not in TARGETS:
        raise ValueError("不支援的翻譯目標語言")
    if legacy is not None and target not in custom:
        # Old factory prompts become the dedicated factory prompt; custom prose
        # stays attached to its original target without being rewritten.
        old_styles = (*_OLD_TRADITIONAL_STYLES, STYLE_INSTRUCTION)
        if target == "zh-Hans":
            old_styles += _OLD_SIMPLIFIED_STYLES
        if legacy not in old_styles:
            variants[target] = validate_style(legacy)
    return variants


def translation_preferences(settings):
    target = settings.get("target_language") or "zh-Hant"
    if not isinstance(target, str) or target not in TARGETS:
        raise ValueError("不支援的翻譯目標語言")
    style = (
        validate_style(settings["translation_style"])
        if "global_glossary" in settings and "translation_style" in settings
        else style_variants(settings)[target]
    )
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


def person_name_span(source, start, end):
    """Avoid applying a short remembered name inside a longer Japanese name."""
    tail = source[end:]
    first, last = source[start], source[end - 1]
    before, after = source[start - 1 : start] if start else "", source[end : end + 1]
    if before and (
        (re.fullmatch(r"[ァ-ヺーA-Za-z]", first) and re.fullmatch(r"[ァ-ヺーA-Za-z]", before))
        or (re.fullmatch(r"[一-龯々]", first) and re.fullmatch(r"[一-龯々]", before))
    ):
        return False
    if tail.startswith(NAME_SUFFIXES):
        return True
    if re.fullmatch(r"[ぁ-ゖー]+", source[start:end]) and re.fullmatch(r"[一-龯々]", after):
        # A remembered kana name must not turn an adverb such as こう思う into a person.
        return False
    if after:
        if re.fullmatch(r"[ァ-ヺーA-Za-z]", last) and re.fullmatch(r"[ァ-ヺーA-Za-z]", after):
            return False
        if re.fullmatch(r"[一-龯々]", last) and re.fullmatch(r"[一-龯々]", after):
            return False
        if re.fullmatch(r"[ぁ-ゖー]", last) and re.fullmatch(r"[ぁ-ゖー]", after):
            if not tail.startswith(
                ("は", "が", "を", "に", "と", "も", "の", "って", "へ", "です", "だ", "ね")
            ):
                return False
    return True


def person_name_occurs(name, source):
    return any(
        person_name_span(source, *m.span()) for m in re.finditer(re.escape(kana_key(name)), kana_key(source))
    )


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
    # Machine name memory is the lowest-priority terminology source. An explicit
    # user choice (including disabling a name) applies to all its known aliases.
    automatic = {}
    explicit = {**grouped(common), **grouped(local)}
    for entry in settings.get("automatic_names", []):
        choices = [
            value for alias in entry["aliases"] for value in explicit.get(kana_key(alias), {}).values()
        ]
        if choices and (None in choices or len(set(choices)) != 1):
            continue
        value = choices[0] if choices else entry["zh"]
        automatic.update({alias: value for alias in entry["aliases"]})
    auto_groups = grouped(automatic)
    resolved = {**auto_groups, **explicit}
    # Include disabled/overridden long forms when matching, so masking a word
    # does not accidentally activate a shorter global term inside that word.
    pattern = "|".join(re.escape(word) for word in sorted(resolved, key=len, reverse=True))
    terms = {}
    for source in sources:
        for match in re.finditer(pattern, kana_key(source)) if pattern else []:
            if (
                match.group() in auto_groups
                and match.group() not in explicit
                and not person_name_span(source, *match.span())
            ):
                continue
            word = source[match.start() : match.end()]
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
