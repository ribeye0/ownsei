import pytest

from onsei.webvtt import VttCue, parse_vtt, write_vtt


def test_reference_metadata_is_separate_from_dialogue():
    doc = parse_vtt(
        "\ufeffWEBVTT\r\n\r\nNOTE reference\r\nkept\r\n\r\nSTYLE\r\n::cue {color:white}\r\n\r\n"
        "first\r\n00:01.100 --> 00:02.200 align:start\r\n<v 六子><i>你好</i> &amp; 再見\r\n第二行\r\n\r\n"
        "00:02.000 --> 00:03.000\r\n凜：問題：怎麼了？\r\n"
    )
    assert len(doc.blocks) == 2
    assert doc.cues[0].settings == "align:start"
    assert doc.cues[0].reference_text() == ("你好 & 再見\n第二行", "六子")
    assert doc.cues[1].reference_text(("六子", "凜")) == ("問題：怎麼了？", "凜")
    assert doc.cues[1].reference_text() == ("凜：問題：怎麼了？", None)


def test_plain_model_text_roundtrips_without_becoming_markup():
    cues = [VttCue(3600123, 3601999, '日文\n中文 <v 某人> & "引用" -->', "stable-id")]
    text = write_vtt(cues)
    assert "01:00:00.123" in text and "&lt;v 某人&gt;" in text
    parsed = parse_vtt(text).cues[0]
    assert parsed.identifier == "stable-id"
    assert parsed.reference_text() == (cues[0].text, None)
    assert parsed.start_ms == 3600123


@pytest.mark.parametrize(
    "content",
    [
        "wrong\n\n00:00.000 --> 00:01.000\nx",
        "WEBVTT\n\n00:60.000 --> 01:01.000\nx",
        "WEBVTT\n\n00:01.000 --> 00:01.000\nx",
        "WEBVTT\n\n00:02.000 --> 00:03.000\nx\n\n00:01.000 --> 00:02.000\nx",
    ],
)
def test_invalid_timing_does_not_silently_export(content):
    with pytest.raises(ValueError):
        parse_vtt(content)


def test_legacy_punctuation_only_rows_do_not_break_export(tmp_path):
    from onsei.domain import Cue, Track
    from onsei.export import export_track
    from onsei.storage import Store

    store = Store.create(tmp_path / "work", "Work")
    track = Track(
        id="t",
        name="Track",
        source="fake.wav",
        sha256="hash",
        duration_ms=3000,
        sample_rate=16000,
        channels=1,
    )
    store.save_track(track)
    cues = [
        Cue(
            id=f"c{i}",
            track_id="t",
            version="v",
            ordinal=i,
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            ja_raw=text,
        )
        for i, text in enumerate(["はい", "。", "またね"])
    ]
    store.replace_cues(track, "v", cues)
    paths = export_track(store, "t", store.path / "exports", japanese_only=True)
    vtt = next(p for p in paths if p.suffix == ".vtt")
    assert len(parse_vtt(vtt.read_text()).cues) == 2
    assert len(store.cues("t")) == 3
    audit = next(p for p in paths if p.suffix == ".jsonl")
    assert len(audit.read_text().splitlines()) == 3


def test_empty_legacy_row_cannot_hide_an_untranslated_chinese_result(tmp_path):
    from onsei.domain import Cue, Track
    from onsei.export import export_track
    from onsei.storage import Store

    store = Store.create(tmp_path / "work", "Work")
    track = Track(
        id="t",
        name="Track",
        source="fake.wav",
        sha256="hash",
        duration_ms=3000,
        sample_rate=16000,
        channels=1,
    )
    store.save_track(track)
    store.replace_cues(
        track,
        "v",
        [
            Cue(id="c0", track_id="t", version="v", ordinal=0, start_ms=0, end_ms=900, ja_raw="はい"),
            Cue(id="c1", track_id="t", version="v", ordinal=1, start_ms=1000, end_ms=1900, ja_raw="。"),
        ],
    )
    store.edit("c0", {"zh_override": "好"})
    with pytest.raises(ValueError, match="translations missing"):
        export_track(store, "t", store.path / "exports")
    assert list((store.path / "exports").glob("*.ja.vtt"))
    assert not list((store.path / "exports").glob("*.zh-TW.vtt"))
