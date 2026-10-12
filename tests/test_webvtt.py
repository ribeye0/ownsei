import pytest

from onsei.webvtt import VttCue, parse_vtt, write_vtt


def test_reference_metadata_is_separate_from_dialogue():
    doc = parse_vtt("\ufeffWEBVTT\r\n\r\nNOTE reference\r\nkept\r\n\r\nSTYLE\r\n::cue {color:white}\r\n\r\n"
                    "first\r\n00:01.100 --> 00:02.200 align:start\r\n<v 六子><i>你好</i> &amp; 再見\r\n第二行\r\n\r\n"
                    "00:02.000 --> 00:03.000\r\n凜：問題：怎麼了？\r\n")
    assert len(doc.blocks) == 2
    assert doc.cues[0].settings == "align:start"
    assert doc.cues[0].reference_text() == ("你好 & 再見\n第二行", "六子")
    assert doc.cues[1].reference_text(("六子", "凜")) == ("問題：怎麼了？", "凜")
    assert doc.cues[1].reference_text() == ("凜：問題：怎麼了？", None)


def test_plain_model_text_roundtrips_without_becoming_markup():
    cues = [VttCue(3600123, 3601999, '日文\n中文 <v 某人> & "引用" -->', 'stable-id')]
    text = write_vtt(cues)
    assert "01:00:00.123" in text and "&lt;v 某人&gt;" in text
    parsed = parse_vtt(text).cues[0]
    assert parsed.identifier == 'stable-id'
    assert parsed.reference_text() == (cues[0].text, None)
    assert parsed.start_ms == 3600123


@pytest.mark.parametrize('content', [
    'wrong\n\n00:00.000 --> 00:01.000\nx',
    'WEBVTT\n\n00:60.000 --> 01:01.000\nx',
    'WEBVTT\n\n00:01.000 --> 00:01.000\nx',
    'WEBVTT\n\n00:02.000 --> 00:03.000\nx\n\n00:01.000 --> 00:02.000\nx',
])
def test_invalid_timing_does_not_silently_export(content):
    with pytest.raises(ValueError):
        parse_vtt(content)
