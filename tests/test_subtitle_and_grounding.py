import json

import pytest

from onsei.domain import Cue, subtitle_ja, subtitle_zh
from onsei.translation_adapters import normalize_grounded_reply
from onsei.translation import validate_content
from onsei.translation_contract import output_grammar


@pytest.mark.parametrize(
    ("raw", "rendered"),
    [
        ("はい。", "はい"),
        ("はい.", "はい"),
        ("はい．", "はい"),
        ("「はい。」", "「はい」"),
        ("はい。\n", "はい\n"),
        ("え？", "え？"),
        ("え?", "え?"),
        ("うん……", "うん……"),
        ("うん......", "うん......"),
        ("1.5です。", "1.5です"),
        ("はい。次です。", "はい。次です"),
    ],
)
def test_subtitle_end_stops_preserve_ellipsis_questions_and_raw(raw, rendered):
    assert subtitle_ja(raw) == rendered
    c = Cue(id="c", track_id="t", version="v", ordinal=0, start_ms=0, end_ms=1000, ja_raw=raw)
    assert c.ja == rendered and c.ja_raw == raw
    c.ja_override = "手動。"
    assert c.ja == "手動。"


@pytest.mark.parametrize(
    ("raw", "rendered"),
    [
        ("知道了。", "知道了"),
        ("知道了.", "知道了"),
        ("知道了．", "知道了"),
        ("可是，", "可是"),
        ("可是,", "可是"),
        ("可是、", "可是"),
        ("「知道了。」", "「知道了」"),
        ("（知道了，）", "（知道了）"),
        ("“知道了。”", "“知道了”"),
        ('"知道了。"。', '"知道了"'),
        ("等等，\n知道了。\r\n", "等等\n知道了\r\n"),
        ("知道了。，  ", "知道了  "),
        ("知道了。下一句，還沒說完，", "知道了。下一句，還沒說完"),
        ("真的？", "真的？"),
        ("真的?", "真的?"),
        ("等等！", "等等！"),
        ("等等!", "等等!"),
        ("嗯……", "嗯……"),
        ("嗯...", "嗯..."),
        ("嗯．．．", "嗯．．．"),
        ("嗯...，", "嗯..."),
        ("數值是1.5。", "數值是1.5"),
        ("數值是1.5", "數值是1.5"),
    ],
)
def test_chinese_subtitle_punctuation_keeps_raw_and_manual(raw, rendered):
    c = Cue(id="c", track_id="t", version="v", ordinal=0, start_ms=0, end_ms=1000, ja_raw="はい", zh_auto=raw)
    assert subtitle_zh(raw) == rendered
    assert c.zh == rendered and c.zh_auto == raw
    assert subtitle_zh(rendered) == rendered
    c.zh_override = "人工校對。"
    assert c.zh == "人工校對。"


def test_source_echo_id_order_and_exact_source_are_required():
    lines = [{"id": "1", "text": "当然じゃないですか?"}, {"id": "2", "text": "ごめん"}]
    rows = [{"id": r["id"], "source": r["text"], "text": "測試"} for r in lines]
    assert json.loads(normalize_grounded_reply(json.dumps(rows), lines))[0] == {"id": "1", "text": "測試"}
    for bad in (list(reversed(rows)), [{"id": "1", "source": "ごめん", "text": "測試"}, rows[1]], [rows[0]]):
        with pytest.raises(ValueError):
            normalize_grounded_reply(json.dumps(bad), lines)
    grammar = output_grammar(["1", "2"], sources=[r["text"] for r in lines])
    assert '\\"source\\"' in grammar and "当然じゃないですか?" in grammar
    with pytest.raises(ValueError):
        output_grammar(["1"], sources=[])


def test_content_hints_allow_glossary_without_rejecting_batch():
    data = {"lines": [{"id": str(i), "text": f"違う台詞{i}"} for i in range(3)], "glossary": {}}
    flags = validate_content({str(i): "大家對我來說都是重要的朋友。" for i in range(3)}, data)
    assert all("translation_repetition_question" in f for f in flags.values())
    data["lines"] = [{"id": "1", "text": "あ、どこちゃん。"}]
    assert validate_content({"1": "啊，朵可。"}, data)["1"] == []
    assert validate_content({"1": "啊，朵可醬。"}, data)["1"] == []
    data["glossary"] = {"どこちゃん": "朵可小姐"}
    assert validate_content({"1": "啊，朵可小姐。"}, data)["1"] == []
    data = {"lines": [{"id": "1", "text": "ホームズくんのこと?"}], "glossary": {}}
    assert validate_content({"1": "是指霍爾姆斯學長嗎？"}, data)["1"] == []
    assert validate_content({"1": "是指霍爾姆斯君嗎？"}, data)["1"] == []


@pytest.mark.parametrize(
    ("source", "text"),
    [
        ("でも、悪いことをしてちゃんとバチが当たるなら、", "可是，如果做了壞事真的會受到報應的話，"),
        ("ちゃんとしてください", "請認真一點"),
        ("お兄ちゃん", "哥哥"),
        ("ロコちゃんと仲良く", "和羅可醬好好相處"),
    ],
)
def test_chan_adverbs_and_kinship_are_not_personal_name_honorifics(source, text):
    validate_content({"1": text}, {"lines": [{"id": "1", "text": source}], "glossary": {}})


def test_explicit_terms_are_hints_and_user_override_takes_priority():
    data = {"lines": [{"id": "1", "text": "ホームズくんのこと?"}], "glossary": {"ホームズくん": "霍爾姆斯君"}}
    assert validate_content({"1": "霍尔姆斯君？"}, data)["1"] == []
    assert set(validate_content({"1": "霍爾姆斯學長？"}, data)["1"]) == {"translation_glossary_question"}
    assert validate_content({"1": "霍爾姆斯君喜歡學長？"}, data)["1"] == []
    data["glossary"] = {"ホームズくん": "霍爾姆斯學長"}
    assert validate_content({"1": "霍爾姆斯學長？"}, data)["1"] == []


def test_honorific_word_matching_does_not_veto_contextual_relationships():
    # The previous regex flagged any 學長 beside くん, even when it referred to
    # someone else. No character-role inference belongs in a string checker.
    data = {
        "lines": [{"id": "1", "text": "ホームズくんはあの方を待っています"}],
        "before": ["あの方は先輩です"],
        "glossary": {},
    }
    assert validate_content({"1": "霍爾姆斯君在等學長"}, data)["1"] == []
