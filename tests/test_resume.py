import json
from pathlib import Path

import pytest

import onsei.asr as module
from onsei.util import atomic_json


def test_resume_skips_finished_worker_units(tmp_path, monkeypatch):
    directory = tmp_path / "cache"
    directory.mkdir()
    done = directory / "0.json"
    atomic_json(done, {"data": {}})
    items = [{"index": i, "output": str(directory / f"{i}.json")} for i in range(3)]
    requests = []

    class Process:
        stdout = iter([])

        def __init__(self, *args, **kwargs):
            request = json.loads(Path(args[0][-1]).read_text())
            requests.append(request)
            for item in request["items"]:
                atomic_json(Path(item["output"]), {"data": {}})

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(module.backend, "device", lambda _: "CPU")
    monkeypatch.setattr(module.backend, "environment", lambda _: {})
    monkeypatch.setattr(module.subprocess, "Popen", Process)
    module.invoke(Path("/fake/haas-ja-v1.0"), Path("/fake/audio"), items, directory, lambda _: None)
    assert [i["index"] for i in requests[0]["items"]] == [1, 2]
    module.invoke(Path("/fake/haas-ja-v1.0"), Path("/fake/audio"), items, directory, lambda _: None)
    assert len(requests) == 1


def test_failed_alignment_cannot_export_subtitles(tmp_path):
    from onsei.domain import Cue, Track
    from onsei.storage import Store
    from onsei.export import export_track

    s = Store.create(tmp_path / "p", "test")
    t = Track(id="t", name="t", source="/fake", sha256="h", duration_ms=1000, sample_rate=16000, channels=1)
    s.save_track(t)
    c = Cue(
        id="c",
        track_id="t",
        version="v",
        ordinal=0,
        start_ms=0,
        end_ms=500,
        ja_raw="はい",
        zh_auto="好",
        translation_status="translated",
        timing_status="alignment_failed",
    )
    s.replace_cues(t, "v", [c])
    with pytest.raises(ValueError):
        export_track(s, "t", s.path / "exports")
    assert list((s.path / "exports").glob("*.ja.txt"))
    assert not list((s.path / "exports").glob("*.srt"))
    assert not list((s.path / "exports").glob("*bilingual-timed.md"))
