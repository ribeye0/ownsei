import pytest
from onsei.asr_filter import normalize_filter, filter_spans


def test_filter_keeps_candidates_intact_and_reports_low_support():
    spans = [
        {"text": "はい", "start_ms": 100, "end_ms": 500},
        {"text": "noise", "start_ms": 1000, "end_ms": 1500},
        {"text": "short", "start_ms": 1600, "end_ms": 1800},
    ]
    kept, dropped = filter_spans(spans, [[100, 500], [1700, 2000]], 0.6)
    assert [s["text"] for s in kept] == ["はい"]
    assert kept[0]["start_ms"] == 100 and kept[0]["end_ms"] == 500
    assert [s["text"] for s in dropped] == ["noise", "short"]
    assert spans[0] == {"text": "はい", "start_ms": 100, "end_ms": 500}
    assert len(filter_spans(spans, [], 0)[0]) == 3
    assert len(filter_spans(spans, [[100, 500], [1700, 2000]], 0.5)[0]) == 2


@pytest.mark.parametrize(
    "options",
    [
        {"min_overlap": float("nan")},
        {"min_overlap": 1.1},
        {"enabled": "yes"},
        {"settings": {"threshold": 0}},
        {"unknown": True},
    ],
)
def test_invalid_options_rejected(options):
    with pytest.raises(ValueError):
        normalize_filter(options)


def test_disabled_filter_preserves_legacy_version_identity():
    assert not normalize_filter()["enabled"]
    assert (
        normalize_filter({"enabled": True, "settings": {"threshold": 0.65, "max_gain_db": 0}})["settings"][
            "threshold"
        ]
        == 0.65
    )


def test_api_pins_filter_options_and_rejects_bad_values(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import onsei.api as api
    from onsei.domain import Track

    monkeypatch.setattr(api, "REGISTRY", tmp_path / "index.db")
    monkeypatch.setattr(api, "start_job", lambda *_: None)
    client = TestClient(api.app, base_url="http://127.0.0.1")
    pid = client.post("/api/projects", json={"title": "filter", "path": str(tmp_path / "project")}).json()[
        "id"
    ]
    api.project(pid).save_track(
        Track(
            id="t",
            name="test",
            source="/fake.wav",
            sha256="a",
            duration_ms=2000,
            sample_rate=16000,
            channels=1,
        )
    )
    body = {
        "action": "transcribe",
        "asr": "haas-ja-v1.0",
        "asr_filter": {
            "enabled": True,
            "settings": {"threshold": 0.65, "max_gain_db": 0},
            "min_overlap": 0.5,
        },
    }
    response = client.post(f"/api/projects/{pid}/jobs", json=body)
    assert response.status_code == 200, response.text
    snapshot = response.json()["options"]["asr_filter"]
    assert snapshot["settings"]["threshold"] == 0.65 and snapshot["min_overlap"] == 0.5
    body["asr_filter"]["settings"]["threshold"] = 2
    assert client.post(f"/api/projects/{pid}/jobs", json=body).status_code == 422


def test_higher_threshold_and_minimum_duration_reject_short_low_confidence_events():
    from onsei.vad_worker import Intervals
    from onsei.vad_settings import normalize_settings

    def detect(threshold, min_ms):
        collector = Intervals(
            2000,
            normalize_settings("firered", {"threshold": threshold, "min_speech_ms": min_ms, "pad_ms": 0}),
        )
        for t in range(5, 2000, 10):
            collector.feed(0.5 if 100 <= t < 250 else 0.9 if 1000 <= t < 1500 else 0, t)
        return collector.finish()

    assert len(detect(0.3, 100)) == 2
    assert detect(0.65, 300) == [[1000, 1500]]


def test_cached_filter_report_is_visible_in_full_job_log(tmp_path):
    from onsei.asr_filter import detect_and_filter
    from onsei.storage import Store
    from onsei.joblogs import read_log
    from onsei.util import digest, atomic_json

    store = Store.create(tmp_path / "p", "filter")
    directory = store.path / "cache/raw"
    directory.mkdir(parents=True)
    options = normalize_filter({"enabled": True})
    folder = directory / ("speech-filter-" + digest(options)[:12])
    folder.mkdir()
    atomic_json(folder / "vad.json", {"spans_ms": [[100, 500]]})
    events = []
    candidates = [
        {"text": "kept", "start_ms": 100, "end_ms": 500},
        {"text": "removed", "start_ms": 1000, "end_ms": 1500},
    ]
    kept, metadata = detect_and_filter(
        tmp_path / "audio.wav", directory, 2000, candidates, options, events.append
    )
    assert len(kept) == 1 and metadata["dropped"] == 1
    path = events[-1]["log_source"]
    assert path.endswith(".log")
    text = read_log(
        store,
        {
            "id": "test",
            "action": "transcribe",
            "status": "completed",
            "log_sources": [
                {"path": str(__import__("pathlib").Path(path).relative_to(store.path)), "start": 0}
            ],
        },
    )
    assert "removed" in text and "vad_speech_fraction" in text
