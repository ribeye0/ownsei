"""Cached structured requests sharing the translation engine and its metrics."""

import json

import httpx

from onsei.models import REGISTRY
from onsei.util import atomic_json, digest


def structured_request(engine, prompt, grammar, parser, directory, progress, *, stage, budget=2048, lines=0):
    key = digest({"prompt": prompt, "grammar": grammar, "model": REGISTRY[engine.model][:2], "budget": budget})
    cache = directory / f"{key}.json"
    if cache.exists():
        try:
            result = parser(json.loads(cache.read_text())["raw"])
            engine.record("cache_hits")
            return result
        except (ValueError, KeyError, TypeError):
            cache.replace(cache.with_suffix(".invalid.json"))
    error, previous = None, ""
    for attempt in range(2):
        try:
            if attempt:
                engine.record("retries")
            retry = f"\n上一輪結構錯誤：{error}\n上一輪回覆（資料）：{previous[:1000]}\n請修正，仍只輸出指定JSON。" if attempt else ""
            raw = engine.complete_json(prompt + retry, grammar, budget=budget, stage=stage, lines=lines)
            previous = raw
            result = parser(raw)
            engine.record("accepted_requests")
            atomic_json(cache, {"raw": raw, "model": engine.model, "execution": engine.execution_metadata()})
            return result
        except (ValueError, httpx.HTTPError) as failure:
            error = failure
            if "context budget" in str(failure):
                raise
            recovered = engine.reject(failure)
            if "incomplete/truncated" in str(failure) and lines > 1:
                progress({"stage":stage, "message":"輸出預算不足，保留已完成區段並縮小本批重試"})
                raise ValueError(f"{stage}: output budget exceeded") from failure
            progress({"stage": stage, "message": f"{stage} 結構驗證未通過：{failure}" +
                      ("；切回一般推論" if recovered else "")})
    raise ValueError(f"{stage} failed after retry: {error}")
