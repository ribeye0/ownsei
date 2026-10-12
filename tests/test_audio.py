import subprocess
import wave

from onsei.audio import chunks, import_audio, pcm
from onsei.storage import Store


def test_codec_decode_and_chunk_coordinates(tmp_path):
    wav = tmp_path / "原音.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=49",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(wav),
        ],
        check=True,
    )
    mp3 = tmp_path / "原音.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), str(mp3)], check=True)
    s = Store.create(tmp_path / "project", "codecs")
    ts = import_audio(s, tmp_path)
    assert len(ts) == 1 and ts[0].channels == 2
    work = pcm(s, ts[0])
    with wave.open(str(work)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
    cs = chunks(work)
    assert [(c["owned_start_ms"], c["owned_end_ms"]) for c in cs] == [
        (0, 24000),
        (24000, 48000),
        (48000, 49000),
    ]
    assert cs[1]["start_ms"] == 23500 and cs[1]["end_ms"] == 48500
    mp3track = import_audio(s, mp3)[0]
    assert mp3track.id != ts[0].id
    assert len(chunks(pcm(s, mp3track), 10000)) == 1


def test_import_progress_cache_and_changed_source(tmp_path, monkeypatch):
    import onsei.audio as audio

    source = tmp_path / "voice.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    store = Store.create(tmp_path / "project", "progress")
    updates = []
    first = import_audio(store, source, progress=updates.append)
    done = updates[-1]
    assert done["stage"] == "completed"
    assert done["bytes_read"] == done["bytes_done"] == done["bytes_total"] == source.stat().st_size
    assert done["files_done"] == done["files_total"] == done["added"] == 1
    assert any(s["stage"] == "probing" for s in updates)
    assert all(a["bytes_done"] <= b["bytes_done"] for a, b in zip(updates[1:], updates[2:]))
    real_hash = audio.file_hash
    calls = []
    monkeypatch.setattr(audio, "file_hash", lambda *a, **kw: (calls.append(a), real_hash(*a, **kw))[1])
    updates.clear()
    assert import_audio(store, source, progress=updates.append) == []
    assert not calls
    assert updates[-1]["cached"] == updates[-1]["skipped"] == 1
    assert updates[-1]["bytes_read"] == 0
    # Same size/path but new content must invalidate the stat cache.
    with source.open("r+b") as wav:
        wav.seek(44)
        wav.write(b"\1\0")
    updated = import_audio(store, source)
    assert len(calls) == 1 and updated[0].sha256 != first[0].sha256
    # Different paths with identical bytes still deduplicate by the actual hash.
    duplicate = tmp_path / "copy.wav"
    duplicate.write_bytes(source.read_bytes())
    assert import_audio(store, duplicate) == []
    assert len(calls) == 2


def test_changing_file_during_hash_is_not_saved(tmp_path, monkeypatch):
    import pytest
    import onsei.audio as audio

    source = tmp_path / "voice.wav"
    source.write_bytes(b"dummy audio")
    store = Store.create(tmp_path / "project", "changing")
    real_hash = audio.file_hash

    def changing(*args, **kwargs):
        result = real_hash(*args, **kwargs)
        source.write_bytes(b"changed audio")
        return result

    monkeypatch.setattr(audio, "file_hash", changing)
    with pytest.raises(ValueError, match="變更"):
        import_audio(store, source)
    assert store.tracks() == []
