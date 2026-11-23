from onsei.storage import Store


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


def test_stage_progress_persists_independently_through_failure_and_resume(tmp_path, monkeypatch):
    import onsei.runner as runner
    import onsei.asr as asr
    import onsei.export as exports
    import onsei.translation_pipeline as translation
    from onsei.domain import Cue, Track
    from onsei.util import file_hash

    monkeypatch.setattr(runner, "STATE", tmp_path / "state")
    store = Store.create(tmp_path / "p", "progress")
    source = tmp_path / "audio.wav"
    source.write_bytes(b"mocked audio")
    track = Track(
        id="t",
        name="test",
        source=str(source),
        sha256=file_hash(source),
        duration_ms=1000,
        sample_rate=16000,
        channels=1,
    )
    store.save_track(track)
    recognized, attempts = [], []

    class Metrics:
        data = {}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    def recognize(s, track_id, model, limit, progress):
        recognized.append(track_id)
        progress({"fraction": 0.5})
        s.replace_cues(
            s.track(track_id),
            "v",
            [Cue(id="c", track_id="t", version="v", ordinal=0, start_ms=0, end_ms=900, ja_raw="はい")],
        )
        progress({"fraction": 1})

    def translate(s, track_id, model, progress, **kwargs):
        progress({"fraction": 0.4})
        progress({"fraction": 0.2})  # A retry must not move a stage backwards.
        attempts.append(track_id)
        if len(attempts) == 1:
            raise RuntimeError("fixture translation failure")
        return {}

    monkeypatch.setattr(runner, "GpuMetrics", Metrics)
    monkeypatch.setattr(asr, "transcribe", recognize)
    monkeypatch.setattr(translation, "process_translation", translate)
    monkeypatch.setattr(exports, "export_track", lambda *args, **kwargs: [])
    job = store.new_job("run", {"track_ids": ["t"]})
    runner.run_job(store, job["id"])
    failed = store.job(job["id"])
    assert failed["status"] == "partial"
    assert failed["stages"]["t"]["transcribe"]["progress"] == 1
    assert failed["stages"]["t"]["translate"]["progress"] == 0.4
    assert failed["stages"]["t"]["translate"]["status"] == "failed"
    resumed = runner.resume_job(store, failed)
    runner.run_job(store, resumed["id"])
    done = store.job(resumed["id"])
    assert done["status"] == "completed"
    assert recognized == ["t"]
    assert all(v["progress"] == 1 for v in done["stages"]["t"].values())
    assert done["stages"]["t"]["transcribe"]["resumed_from"] == job["id"]
