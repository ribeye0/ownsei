from fastapi.testclient import TestClient
import pytest

import onsei.api as service


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8877"])
def test_loopback_hosts_allow_normal_cli_and_browser_requests(host, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "REGISTRY", tmp_path / "index.db")
    client = TestClient(service.app, base_url=f"http://{host}")
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/projects", headers={"Origin": f"http://{host}"}).status_code == 200
    assert client.get("/api/projects", headers={"Origin": "http://untrusted.example"}).status_code == 403


@pytest.mark.parametrize("host", ["untrusted.example", "localhost.untrusted.example", "192.168.1.20"])
def test_untrusted_host_rejected_even_with_matching_origin(host, tmp_path, monkeypatch):
    registry = tmp_path / "index.db"
    monkeypatch.setattr(service, "REGISTRY", registry)
    client = TestClient(service.app, base_url=f"http://{host}")
    for headers in ({}, {"Origin": f"http://{host}"}, {"X-Forwarded-Host": "localhost"}):
        assert client.get("/api/projects", headers=headers).status_code == 400
        assert client.post("/api/projects", json={"title": "blocked"}, headers=headers).status_code == 400
    assert not registry.exists()


def test_local_project_edit_export_and_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "REGISTRY", tmp_path / "index.db")
    client = TestClient(service.app, base_url="http://127.0.0.1")
    r = client.post("/api/projects", json={"title": "測試", "path": str(tmp_path / "project")})
    assert r.status_code == 200
    p = r.json()["id"]
    assert client.get("/api/projects").json()[0]["title"] == "測試"
    assert client.get(f"/api/projects/{p}").json()["tracks"] == []
    assert (
        client.post("/api/projects", json={"title": "same", "path": str(tmp_path / "project")}).json()["id"]
        == p
    )
    assert client.patch(f"/api/projects/{p}/settings", json={"glossary": {"先輩": "學長"}}).status_code == 200
    assert client.patch(f"/api/projects/{p}/settings", json={"glossary": {"先輩": 123}}).status_code == 422
    assert client.patch(f"/api/projects/{p}/settings", json={"notes": None}).status_code == 422
    assert (
        client.post(
            "/api/projects", json={"title": "bad"}, headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert client.get(f"/api/projects/{p}/exports/missing.srt").status_code == 404
    assert client.post(f"/api/projects/{p}/jobs", json={"action": "invalid"}).status_code == 422


def test_all_asr_models_accepted_and_non_asr_rejected(tmp_path, monkeypatch):
    from onsei.models import ASR_MODELS

    monkeypatch.setattr(service, "REGISTRY", tmp_path / "index.db")
    monkeypatch.setattr(service, "start_job", lambda *_: None)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    p = client.post("/api/projects", json={"title": "ASR", "path": str(tmp_path / "p")}).json()["id"]
    from onsei.domain import Track

    service.project(p).save_track(
        Track(
            id="t",
            name="fixture",
            source="/fake.wav",
            sha256="h",
            duration_ms=1000,
            sample_rate=16000,
            channels=1,
        )
    )
    for model in ASR_MODELS:
        result = client.post(f"/api/projects/{p}/jobs", json={"action": "transcribe", "asr": model})
        assert result.status_code == 200 and result.json()["options"]["asr"] == model
    for model in ["unknown", "aligner", "index-translate-9b"]:
        assert client.post(f"/api/projects/{p}/jobs", json={"asr": model}).status_code == 422


def test_audio_openapi_distinguishes_playback_and_headers(monkeypatch):
    # Regenerate the schema even when a previous test requested it.
    monkeypatch.setattr(service.app, "openapi_schema", None)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    audio = client.get("/openapi.json").json()["paths"]["/api/projects/{p}/tracks/{t}/audio"]
    assert audio["get"]["operationId"] != audio["head"]["operationId"]


def test_audio_byte_ranges_head_and_missing_source(tmp_path, monkeypatch):
    from onsei.audio import import_audio
    import wave

    monkeypatch.setattr(service, "REGISTRY", tmp_path / "index.db")
    client = TestClient(service.app, base_url="http://127.0.0.1")
    p = client.post("/api/projects", json={"title": "media", "path": str(tmp_path / "project")}).json()["id"]
    source = tmp_path / "sound.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(3)
        wav.setframerate(48000)
        wav.writeframes(bytes(range(256)) * 600)
    track = import_audio(service.project(p), source)[0]
    data = source.read_bytes()
    url = f"/api/projects/{p}/tracks/{track.id}/audio"
    head = client.head(url)
    assert head.status_code == 200 and head.content == b""
    assert int(head.headers["content-length"]) == len(data)
    assert head.headers["accept-ranges"] == "bytes"
    for start, end in [(0, 4095), (len(data) - 8192, len(data) - 1), (48000, 52095)]:
        response = client.get(url, headers={"Range": f"bytes={start}-{end}"})
        assert response.status_code == 206
        assert response.content == data[start : end + 1]
        assert response.headers["content-range"] == f"bytes {start}-{end}/{len(data)}"
    assert client.get(url, headers={"Range": f"bytes={len(data)}-"}).status_code == 416
    source.unlink()
    assert client.get(url).status_code == 404
