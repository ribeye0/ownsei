from fastapi.testclient import TestClient
import onsei.api as service
from onsei.storage import Store


def test_full_logs_download_ranges_and_path_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "REGISTRY", tmp_path / "index.db")
    client = TestClient(service.app, base_url="http://127.0.0.1")
    p = client.post("/api/projects", json={"title": "log test", "path": str(tmp_path / "project")}).json()[
        "id"
    ]
    s = Store(tmp_path / "project")
    j = s.new_job("realign", {})
    j.update(status="failed", error="Traceback\nComplete failure detail\n")
    (s.path / "logs").mkdir()
    (s.path / "cache").mkdir()
    main = "first line\n" + "progress\n" * 1000 + "LAST LINE\n"
    (s.path / "logs" / f"{j['id']}.log").write_text(main)
    old = "old model invocation\n"
    current = "模型載入診斷\n"
    future = "later invocation\n"
    (s.path / "cache" / "aligner.log").write_text(old + current + future)
    secret = tmp_path / "outside.log"
    secret.write_text("PRIVATE_OUTSIDE")
    (s.path / "cache" / "linked.log").symlink_to(secret)
    j["log_sources"] = [
        {"path": "cache/aligner.log", "start": len(old.encode()), "end": len((old + current).encode())},
        {"path": str(secret), "start": 0},
        {"path": "cache/linked.log", "start": 0},
    ]
    s.save_job(j)
    base = f"/api/projects/{p}/jobs/{j['id']}/log"
    r = client.get(base)
    assert r.status_code == 200
    text = r.json()["text"]
    assert main in text and "Complete failure detail" in text and current in text
    assert old not in text and future not in text and "PRIVATE_OUTSIDE" not in text
    download = client.get(base + "/download")
    assert download.status_code == 200 and download.text == text
    assert "attachment" in download.headers["content-disposition"]
    assert client.get(f"/api/projects/{p}/jobs/missing/log").status_code == 404
    assert client.get(f"/api/projects/{p}/jobs/missing/log/download").status_code == 404


def test_legacy_job_keeps_complete_traceback_without_subprocess_sources(tmp_path):
    from onsei.joblogs import read_log

    s = Store.create(tmp_path / "legacy", "legacy")
    j = s.new_job("realign", {})
    j.update(status="failed", error="unaligned token 0\n" + "details\n" * 1000)
    text = read_log(s, j)
    assert j["error"] in text and "未記錄子程序日誌來源" in text


def test_runner_records_only_current_model_log_range_on_failure(tmp_path, monkeypatch):
    import onsei.runner as runner
    import onsei.asr as asr
    from onsei.domain import Track
    from onsei.joblogs import read_log

    monkeypatch.setattr(runner, "STATE", tmp_path / "state")
    s = Store.create(tmp_path / "p", "test")
    from onsei.util import file_hash

    source = tmp_path / "fixture.wav"
    source.write_bytes(b"mocked model fixture")
    s.save_track(
        Track(
            id="t",
            name="test",
            source=str(source),
            sha256=file_hash(source),
            duration_ms=1000,
            sample_rate=16000,
            channels=1,
        )
    )
    cache = s.path / "cache"
    cache.mkdir()
    log = cache / "whisper.log"
    log.write_text("previous run\n")

    def recognize(_s, _track, _model, _limit, progress):
        progress({"log_source": str(log), "log_start": log.stat().st_size, "message": "loading model"})
        with log.open("a") as file:
            file.write("current CUDA load failed\n")
        raise RuntimeError("model load failure")

    class Metrics:
        data = {}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(asr, "transcribe", recognize)
    monkeypatch.setattr(runner, "GpuMetrics", Metrics)
    job = s.new_job("transcribe", {"track_ids": ["t"]})
    runner.run_job(s, job["id"])
    after = s.job(job["id"])
    with log.open("a") as file:
        file.write("future run\n")
    text = read_log(s, after)
    assert after["status"] == "failed" and after["finished_at"] >= after["started_at"]
    assert "current CUDA load failed" in text and "model load failure" in text
    assert "previous run" not in text and "future run" not in text
