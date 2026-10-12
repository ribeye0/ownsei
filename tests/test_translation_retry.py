import json
from onsei.domain import Cue, Track
from onsei.storage import Store
import onsei.translation as module


def test_split_retry_cache_and_manual_protection(tmp_path, monkeypatch):
    s = Store.create(tmp_path / "p", "test")
    t = Track(
        id="t", name="t", source="/tmp/fake", sha256="h", duration_ms=10000, sample_rate=16000, channels=1
    )
    s.save_track(t)
    cs = [
        Cue(
            id=f"c{i}",
            track_id="t",
            version="v",
            ordinal=i,
            start_ms=i * 1000,
            end_ms=i * 1000 + 500,
            ja_raw="はい",
        )
        for i in range(4)
    ]
    s.replace_cues(t, "v", cs)
    s.edit("c1", {"zh_override": "人工"})
    calls = []

    class Engine:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def generate(self, data):
            calls.append(data)
            if len(data["lines"]) > 1:
                return "[]"
            return json.dumps([{"id": data["lines"][0]["id"], "text": "好的"}], ensure_ascii=False)

    monkeypatch.setattr(module, "Llama", Engine)
    module.translate(s, "t", "hy-mt2-7b", None, lambda _: None)
    assert [c.zh for c in s.cues("t")] == ["好的", "人工", "好的", "好的"]
    count = len(calls)
    # Cache is reused after invalidation with unchanged payload.
    for c in s.cues("t"):
        if c.zh_override is None:
            c.translation_status = "stale"
            s.save_generated(c, c.revision)
    module.translate(s, "t", "hy-mt2-7b", None, lambda _: None)
    # Invalid parent batches are retried, but accepted singleton responses are cached.
    assert len(calls) - count == 6
