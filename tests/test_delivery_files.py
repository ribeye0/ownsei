"""Exact source filenames, independent downloads and deliberate local publishing."""

import json
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from onsei.delivery import prepare_delivery, delivery_download, delivery_file
from onsei.domain import Cue, Track
from onsei.storage import Store


@pytest.fixture
def store(tmp_path):
    store = Store.create(tmp_path / "workspace", "Download test")
    # Decomposed dakuten is intentionally preserved byte-for-byte.
    source = tmp_path / "audio" / " 02：ストリップ ～テスト!？ [a] #%.wav"
    source.parent.mkdir()
    source.write_bytes(b"original audio")
    track = Track(
        id="track",
        name="Renamed display title",
        source=str(source),
        sha256="test",
        duration_ms=5000,
        sample_rate=16000,
        channels=1,
    )
    store.save_track(track)
    store.replace_cues(
        track,
        "version",
        [
            Cue(
                id="cue",
                track_id="track",
                version="version",
                ordinal=0,
                start_ms=100,
                end_ms=1100,
                ja_raw="お帰り",
                zh_auto="歡迎回來",
                translation_status="translated",
            )
        ],
    )
    return store


def selection(store, track="track"):
    return {
        "track_id": track,
        "version": store.track(track).active_version,
        "result_id": store.input_snapshot(track)["result_id"],
    }


def test_single_file_preserves_source_name_and_download_invalidates_on_edit(store):
    result = prepare_delivery(store, [selection(store)], ["bilingual"], ["vtt"])
    name = Path(store.track("track").source).with_suffix(".vtt").name
    assert result["files"] == [name] and not result["has_zip"]
    assert result["downloads"] == [{"index": 0, "name": name, "path": name}]
    path, filename = delivery_file(store, result["id"], 0)
    assert filename == name and "歡迎回來" in path.read_text()
    with pytest.raises(FileNotFoundError):
        delivery_download(store, result["id"])
    with pytest.raises(FileNotFoundError):
        delivery_file(store, result["id"], -1)
    store.edit("cue", {"zh_override": "人工修改"})
    with pytest.raises(ValueError, match="來源已更新"):
        delivery_file(store, result["id"], 0)


@pytest.mark.parametrize(
    "target,tag,text", [("zh-Hant", "zh-tw", "歡迎回來"), ("zh-Hans", "zh-cn", "欢迎回来")]
)
def test_optional_language_tag(store, target, tag, text):
    result = prepare_delivery(store, [selection(store)], ["zh"], ["vtt"], target, language_tag=True)
    stem = Path(store.track("track").source).stem
    assert result["files"] == [f"{stem}.{tag}.vtt"]
    path, _ = delivery_file(store, result["id"], 0)
    assert text in path.read_text()


def test_multiple_languages_use_directories_and_every_file_is_downloadable(store):
    result = prepare_delivery(store, [selection(store)], ["ja", "zh", "bilingual"], ["vtt", "srt"])
    assert len(result["files"]) == 6 and result["has_zip"]
    stem = Path(store.track("track").source).stem
    assert set(result["files"]) == {
        f"{lang}/{stem}.{ext}" for lang in ["ja", "zh-tw", "bilingual"] for ext in ["srt", "vtt"]
    }
    archive, _ = delivery_download(store, result["id"])
    with zipfile.ZipFile(archive) as bundle:
        assert len(bundle.namelist()) == 7
        for file in result["downloads"]:
            path, name = delivery_file(store, result["id"], file["index"])
            assert name == Path(file["path"]).name
            assert path.read_bytes() == bundle.read(file["path"])


@pytest.mark.parametrize("destination", ["source", "sub"])
def test_local_publish_and_existing_manual_subtitle_are_preserved(store, destination):
    source = Path(store.track("track").source)
    result = prepare_delivery(store, [selection(store)], ["bilingual"], ["vtt"], destination=destination)
    target = (source.parent / "sub" if destination == "sub" else source.parent) / source.with_suffix(
        ".vtt"
    ).name
    assert result["tracks"][0]["written"] == [str(target)] and target.exists()
    target.write_text("original human subtitle")
    again = prepare_delivery(store, [selection(store)], ["bilingual"], ["vtt"], destination=destination)
    assert target.read_text() == "original human subtitle"
    assert again["tracks"][0]["write_errors"][0]["path"] == str(target)
    assert delivery_file(store, again["id"], 0)[0].is_file()
    overwrite = prepare_delivery(
        store, [selection(store)], ["bilingual"], ["vtt"], destination=destination, overwrite=True
    )
    assert overwrite["tracks"][0]["written"] == [str(target)]
    assert "歡迎回來" in target.read_text() and source.read_bytes() == b"original audio"


def test_write_failure_still_provides_download(store, monkeypatch):
    import onsei.delivery as module

    def fail(*_args, **_kwargs):
        raise PermissionError("read-only audio folder")

    monkeypatch.setattr(module, "publish_copy", fail)
    result = prepare_delivery(store, [selection(store)], ["bilingual"], ["vtt"], destination="sub")
    assert result["tracks"][0]["write_errors"][0]["error"] == "read-only audio folder"
    assert delivery_file(store, result["id"], 0)[0].is_file()


def test_missing_audio_does_not_recreate_its_directory(store):
    source = Path(store.track("track").source)
    source.unlink()
    source.parent.rmdir()
    result = prepare_delivery(store, [selection(store)], ["bilingual"], ["vtt"], destination="sub")
    assert result["tracks"][0]["write_errors"] and not source.parent.exists()
    assert delivery_file(store, result["id"], 0)[0].is_file()


def test_same_named_tracks_in_zip_and_local_collision_do_not_rename_or_clobber(store):
    first = store.track("track")
    second = first.model_copy(update={"id": "track2", "source": str(Path(first.source).with_suffix(".mp3"))})
    Path(second.source).write_bytes(b"mp3 audio")
    store.save_track(second)
    cue = store.cues("track")[0].model_copy(update={"id": "cue2", "track_id": "track2", "version": "v2"})
    store.replace_cues(second, "v2", [cue])
    result = prepare_delivery(
        store,
        [selection(store), selection(store, "track2")],
        ["bilingual"],
        ["vtt"],
        destination="source",
        overwrite=True,
    )
    expected = Path(first.source).with_suffix(".vtt").name
    assert result["files"] == [f"track-track/{expected}", f"track-track2/{expected}"]
    assert all(t["write_errors"] and not t["written"] for t in result["tracks"])
    assert not Path(first.source).with_suffix(".vtt").exists()
    archive, _ = delivery_download(store, result["id"])
    with zipfile.ZipFile(archive) as bundle:
        assert len(set(bundle.namelist())) == 3
    assert all(delivery_file(store, result["id"], i)[1] == expected for i in range(2))


def test_file_endpoint_uses_exact_utf8_attachment_name_and_validates_source(store, tmp_path, monkeypatch):
    import onsei.api as api

    monkeypatch.setattr(api, "project", lambda _: store)
    client = TestClient(api.app, base_url="http://127.0.0.1")
    selected = selection(store)
    response = client.post("/api/projects/p/deliveries", json={"selections": [selected]})
    assert response.status_code == 200
    result = response.json()
    response = client.get(f"/api/projects/p/deliveries/{result['id']}/files/0")
    assert response.status_code == 200
    assert (
        unquote(response.headers["content-disposition"].split("utf-8''")[1])
        == Path(store.track("track").source).with_suffix(".vtt").name
    )
    assert client.get(f"/api/projects/p/deliveries/{result['id']}/files/99").status_code == 404
    assert client.get("/api/projects/p/deliveries/../files/0").status_code in (404, 422)
    store.edit("cue", {"zh_override": "new"})
    assert client.get(f"/api/projects/p/deliveries/{result['id']}/files/0").status_code == 422


def test_partial_download_validity_is_scoped_to_file(store):
    first = store.track("track")
    second = first.model_copy(
        update={"id": "track2", "source": str(Path(first.source).parent / "another.wav")}
    )
    store.save_track(second)
    store.replace_cues(
        second,
        "v2",
        [store.cues("track")[0].model_copy(update={"id": "cue2", "track_id": "track2", "version": "v2"})],
    )
    result = prepare_delivery(store, [selection(store), selection(store, "track2")], ["bilingual"], ["vtt"])
    store.edit("cue2", {"zh_override": "changed"})
    assert delivery_file(store, result["id"], 0)[0].is_file()
    with pytest.raises(ValueError):
        delivery_download(store, result["id"])
    with pytest.raises(ValueError):
        delivery_file(store, result["id"], 1)


def test_metadata_paths_cannot_escape_delivery_root(store):
    result = prepare_delivery(store, [selection(store)], ["bilingual"], ["vtt"])
    path = store.path / "exports" / ".downloads" / f"{result['id']}.json"
    metadata = json.loads(path.read_text())
    metadata["outputs"][0]["storage"] = "../../project.db"
    path.write_text(json.dumps(metadata))
    with pytest.raises(FileNotFoundError):
        delivery_file(store, result["id"], 0)


def test_sub_symlink_is_not_followed_when_publishing(store, tmp_path):
    source = Path(store.track("track").source)
    other = tmp_path / "unrelated"
    other.mkdir()
    (source.parent / "sub").symlink_to(other, target_is_directory=True)
    result = prepare_delivery(store, [selection(store)], ["bilingual"], ["vtt"], destination="sub")
    assert result["tracks"][0]["write_errors"] and not list(other.iterdir())
    assert delivery_file(store, result["id"], 0)[0].is_file()


def test_legacy_download_also_uses_original_audio_name(store, monkeypatch):
    import onsei.api as api
    from onsei.export import export_track

    monkeypatch.setattr(api, "project", lambda _: store)
    client = TestClient(api.app, base_url="http://127.0.0.1")
    paths = export_track(store, "track", store.path / "exports")
    path = next(p for p in paths if p.name.endswith(".bilingual.vtt"))
    response = client.get("/api/projects/p/exports/" + path.name)
    assert response.status_code == 200
    filename = unquote(response.headers["content-disposition"].split("utf-8''")[1])
    assert filename == Path(store.track("track").source).with_suffix(".vtt").name


@pytest.mark.parametrize(
    "language,text", [("ja", "お帰り"), ("zh", "歡迎回來"), ("bilingual", "お帰り\n歡迎回來")]
)
@pytest.mark.parametrize("kind", ["ass", "lrc"])
def test_ass_and_lrc_support_each_selected_language(store, language, text, kind):
    import pysubs2

    result = prepare_delivery(store, [selection(store)], [language], [kind])
    assert result["tracks"][0]["error"] is None
    assert result["files"] == [Path(store.track("track").source).with_suffix("." + kind).name]
    path, _ = delivery_file(store, result["id"], 0)
    if kind == "ass":
        subtitles = pysubs2.load(str(path))
        assert len(subtitles) == 1
        assert subtitles[0].plaintext == text
        assert (subtitles[0].start, subtitles[0].end) == (100, 1100)
    else:
        assert path.read_text() == "[00:00.10]" + text.replace("\n", " / ") + "\n"
    assert not list((store.path / "exports" / ".downloads").rglob("*.md"))


def test_lrc_simplified_conversion_and_local_publish(store):
    result = prepare_delivery(store, [selection(store)], ["zh"], ["lrc"], "zh-Hans", destination="sub")
    target = (
        Path(store.track("track").source).parent
        / "sub"
        / Path(store.track("track").source).with_suffix(".lrc").name
    )
    assert result["tracks"][0]["written"] == [str(target)]
    assert target.read_text() == "[00:00.10]欢迎回来\n"


def test_lrc_timestamp_carries_minutes_and_preserves_long_recordings():
    from onsei.export import write_lrc
    from onsei.webvtt import VttCue

    assert (
        write_lrc(
            [
                VttCue(3661234, 3662000, " 長音軌 "),
                VttCue(59995, 61000, "日文\n中文"),
                VttCue(0, 100, " \n "),
                VttCue(4, 100, "開頭"),
            ]
        )
        == "[00:00.00]開頭\n[01:00.00]日文 / 中文\n[61:01.23]長音軌\n"
    )


@pytest.mark.parametrize("kind", ["md", "timed.md"])
def test_removed_markdown_formats_are_rejected(store, kind):
    with pytest.raises(ValueError, match="不支援的匯出選項"):
        prepare_delivery(store, [selection(store)], ["bilingual"], [kind])


def test_vtt_only_skips_other_renderers_and_audit(store, monkeypatch):
    import onsei.export as export

    render = export._render
    seen = []

    def inspect(track, cues, directory, *args):
        render(track, cues, directory, *args)
        seen.extend(p.name for p in directory.iterdir())

    monkeypatch.setattr("onsei.delivery._render", inspect)
    prepare_delivery(store, [selection(store)], ["zh"], ["vtt"])
    assert len(seen) == 1 and seen[0].endswith(".zh-TW.vtt")


def test_cleanup_only_rebuildable_pcm_with_successful_export(store):
    from onsei.audio import pcm_use_lock
    from onsei.source import signature
    from onsei.delivery import cleanup_delivery_pcm

    track = store.track("track")
    source = Path(track.source)
    track.source_signature = signature(source)
    store.save_track(track)
    audio = store.path / "audio"
    audio.mkdir(exist_ok=True)
    cache, proxy = audio / "track.wav", audio / "track-stereo.mp3"
    cache.write_bytes(b"cached")
    proxy.write_bytes(b"proxy")
    result = prepare_delivery(store, [selection(store)], ["zh"], ["vtt"])
    assert cache.exists()
    with pcm_use_lock(store, track):
        assert cleanup_delivery_pcm(store, result["id"])["errors"]
    assert cleanup_delivery_pcm(store, result["id"]) == {"bytes": 6, "errors": []}
    assert cleanup_delivery_pcm(store, result["id"]) == {"bytes": 0, "errors": []}
    assert proxy.is_file() and source.is_file()
    cache.write_bytes(b"cached")
    Path(track.source).with_suffix(".vtt").touch()
    failed = prepare_delivery(
        store, [selection(store)], ["zh"], ["vtt"], destination="source", clear_cache=True
    )
    assert failed["tracks"][0]["write_errors"] and cache.exists()
    success = prepare_delivery(store, [selection(store)], ["zh"], ["vtt"], clear_cache=True)
    assert success["cache_cleanup"]["bytes"] == 6 and not cache.exists()
