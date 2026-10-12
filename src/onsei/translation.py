from __future__ import annotations

import json
import hashlib
import os
import socket
import subprocess
import time
import re
from collections import defaultdict
from pathlib import Path

import httpx
from opencc import OpenCC

from onsei.preferences import translation_preferences, converter_for, pin_translation_settings, effective_glossary
from onsei.terminology import enabled_terms
from onsei.domain import Cue
from onsei.models import GGUF, REGISTRY, model_dir
from onsei.storage import Store
from onsei.translation_contract import BATCH_SIZE, CONTEXT_CUES, dependency_window, output_grammar, polluted, natural_batches, split_point
from onsei.translation_runtime import DecodingProfile, decoding_profile, inference_summary
from onsei.util import ROOT, atomic_json, digest

from onsei.translation_adapters import (INSTRUCTION, parse_reply, build_prompt, normalize_native_reply,
                                         normalize_grounded_reply)

PROMPT_VERSION = 9


def model_prompt_version(model):
    return 10 if model == "asmr-qwen3.5-9b" else PROMPT_VERSION


CONFIG = {
    "context": 8192,
    "output_tokens": 3072,
    "batch_size": BATCH_SIZE,
    "context_cues": CONTEXT_CUES,
    "temperature": 0,
    "repeat_penalty": 1.0,
    "enable_thinking": False,
    "protocol": "native-json-grammar",
    "profile_version": 6,
}


def payload(cues: list[Cue], targets: list[Cue], settings: dict, track_name: str = "") -> dict:
    indexes = [c.ordinal for c in targets]
    lo, hi = min(indexes), max(indexes)
    return {
        "instruction": INSTRUCTION,
        "track": track_name,
        **translation_preferences(settings),
        "notes": settings.get("notes", ""),
        "glossary": enabled_terms(settings.get("glossary_entries", settings.get("glossary", {}))),
        "glossary_entries": settings.get("glossary_entries", settings.get("glossary", {})),
        "before": [c.ja for c in cues if lo - CONTEXT_CUES <= c.ordinal < lo],
        "after": [c.ja for c in cues if hi < c.ordinal <= hi + CONTEXT_CUES],
        "lines": [{"id": c.id, "text": c.ja, "start": c.start_ms, "end": c.end_ms} for c in targets],
    }


def validate_content(translations: dict[str, str], data: dict) -> dict[str, list[str]]:
    """Semantic heuristics are review hints, never proof or batch rejection."""
    flags = {row["id"]: [] for row in data["lines"]}
    repeated = defaultdict(set)
    converter = OpenCC("s2t")
    for row in data["lines"]:
        source, text = row["text"], translations[row["id"]]
        zh = converter.convert(text)
        if len(text) >= 10:
            repeated[text].add(source)
        terms = [(ja, converter.convert(target)) for ja, target in data["glossary"].items() if ja in source]
        if any(target not in zh for _, target in terms):
            flags[row["id"]].append("translation_glossary_question")
        # A name suffix cannot establish who is senior to whom. Preserve the
        # dialogue policy in the prompt, without a separate honorific classifier
        # or a regex veto on contextually valid review suggestions.
        if re.search(r"[ぁ-ゖァ-ヺ]{2,}", text):
            flags[row["id"]].append("translation_residual_japanese")
    for row in data["lines"]:
        if len(repeated[translations[row["id"]]]) >= 3:
            flags[row["id"]].append("translation_repetition_question")
    return {key: list(dict.fromkeys(value)) for key, value in flags.items()}


class Llama:
    def __init__(
        self,
        model: str,
        directory: Path,
        *,
        speculative: str | None = None,
        draft_tokens: int = 3,
        allow_fallback: bool = True,
    ):
        self.model = model
        self.directory = directory
        self.profile = decoding_profile(model, speculative, draft_tokens)
        self.requested_profile = self.profile
        self.allow_fallback = allow_fallback
        self.process = None
        self.client = None
        self.log = None
        self.statistics = []
        self.counters = defaultdict(int)
        self.fallbacks = []
        self.startup_seconds = 0.0
        self.started = None
        self.run_id = str(time.time_ns())
        self.last_request = None

    def __enter__(self):
        self.binary = next(iter((ROOT / ".tools/llama").rglob("llama-server")), None)
        if self.binary is None:
            raise ValueError("llama-server missing. Run scripts/setup-llama.sh")
        gguf = model_dir(self.model) / GGUF[self.model]
        if not gguf.exists():
            raise ValueError("Translation model missing; run ownsei models prepare")
        self.gguf = gguf
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started = time.monotonic()
        self.log = (self.directory / "llama.log").open("a")
        try:
            version = subprocess.run(
                [str(self.binary), "--version"], capture_output=True, text=True, timeout=5
            )
            self.engine_version = (version.stdout + version.stderr).strip()
            try:
                self._start_server()
            except (RuntimeError, httpx.HTTPError) as error:
                if not self.fallback(str(error)):
                    raise
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _start_server(self):
        startup = time.monotonic()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.log.write(f"\nOwnsei decoding profile: {json.dumps(self.profile.metadata())}\n")
        self.log.flush()
        env = dict(os.environ)
        libdirs = [str(self.binary.parent)] + sorted(
            {str(p.parent) for p in (ROOT / ".tools/llama").rglob("libcudart.so*")}
        )
        env["LD_LIBRARY_PATH"] = ":".join(libdirs + [env.get("LD_LIBRARY_PATH", "")])
        try:
            self.process = subprocess.Popen(
                [
                    str(self.binary),
                    "-m",
                    str(self.gguf),
                    "-ngl",
                    "99",
                    "-c",
                    str(CONFIG["context"]),
                    "-np",
                    "1",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--jinja",
                    "--no-webui",
                    "--device",
                    "CUDA0",
                    "-t",
                    "8",
                    "-fa",
                    "on",
                    "--reasoning",
                    "off",
                    "--chat-template-kwargs",
                    '{"enable_thinking":false}',
                ]
                + self.profile.flags(),
                stdout=self.log,
                stderr=self.log,
                env=env,
            )
            self.client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180, trust_env=False)
            for _ in range(240):
                if self.process.poll() is not None:
                    raise RuntimeError(f"llama-server exited; see {self.directory / 'llama.log'}")
                try:
                    if self.client.get("/health", timeout=1).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            raise RuntimeError("llama-server did not become ready")
        except BaseException:
            self._stop_server()
            raise
        finally:
            self.startup_seconds += time.monotonic() - startup

    def _stop_server(self):
        if self.client:
            self.client.close()
            self.client = None
        if self.process and self.process.poll() is None:
            from onsei.processes import stop_process

            stop_process(self.process)
        self.process = None

    def fallback(self, reason: str) -> bool:
        if not self.allow_fallback or self.profile.speculative == "none":
            return False
        self.fallbacks.append({"from": self.profile.metadata(), "reason": reason, "time": time.time()})
        self._stop_server()
        self.profile = DecodingProfile()
        self.log.write(f"\nOwnsei acceleration fallback to baseline: {reason}\n")
        self.log.flush()
        self._start_server()
        return True

    def record(self, event: str, **details):
        self.counters[event] += details.get("lines", 1)
        if event in {"accepted_requests", "rejected_requests"} and self.last_request is not None:
            self.last_request.update(
                validation="accepted" if event == "accepted_requests" else "rejected", **details
            )

    def reject(self, error: Exception) -> bool:
        self.record(
            "rejected_requests" if self.last_request is not None else "preflight_rejections", error=str(error)
        )
        # Oversized prompts do not benefit from restarting the inference engine.
        if "incomplete/truncated" in str(error):
            return False
        return self.last_request is not None and self.fallback(str(error))

    def execution_metadata(self):
        return {
            "run_id": self.run_id,
            "engine": getattr(self, "engine_version", None),
            "profile": self.profile.metadata(),
            **{key: self.last_request[key] for key in
               ("request_id", "request_file", "prompt_sha256", "input_tokens", "output_budget", "effective_glossary")
               if self.last_request is not None and key in self.last_request},
        }

    def complete_json(self, prompt: str, grammar: str, *, budget=2048, stage="review", lines=0):
        """Shared native-template inference for reference extraction and patch review."""
        self.last_request = None
        rendered = self.client.post("/apply-template", json={"messages": [{"role": "user", "content": prompt}],
                                    "chat_template_kwargs": {"enable_thinking": False}})
        rendered.raise_for_status()
        tokenized = self.client.post("/tokenize", json={"content": rendered.json()["prompt"]})
        tokenized.raise_for_status()
        if len(tokenized.json()["tokens"]) + budget + 64 > CONFIG["context"]:
            raise ValueError("Prompt exceeds context budget; split batch")
        started = time.monotonic()
        self.last_request = {"lines": lines, "stage": stage, "profile": self.profile.metadata(),
                             "validation": "pending"}
        self.statistics.append(self.last_request)
        try:
            response = self.client.post("/completion", json={"prompt": rendered.json()["prompt"],
                                        "grammar": grammar, "n_predict": budget, "temperature": 0,
                                        "repeat_penalty": 1.0, "cache_prompt": True, "stream": False})
            response.raise_for_status()
        finally:
            self.last_request["seconds"] = round(time.monotonic() - started, 3)
        reply = response.json()
        self.last_request.update(usage={"prompt_tokens": reply.get("tokens_evaluated"),
                                       "completion_tokens": reply.get("tokens_predicted")},
                                 timings=reply.get("timings", {}), finish_reason=reply.get("stop_type"))
        raw = reply.get("content")
        (self.directory / f"{stage}-{time.time_ns()}.reply.txt").write_text(raw or "", encoding="utf-8")
        if not isinstance(raw, str) or reply.get("stopped_limit") or reply.get("stop_type") == "limit":
            raise ValueError("Structured output incomplete/truncated")
        return raw

    def __exit__(self, *_):
        try:
            self._stop_server()
        finally:
            if self.log:
                self.log.close()
            if self.started is not None:
                wall = time.monotonic() - self.started
                report = {
                    "model": self.model,
                    "revision": REGISTRY[self.model][1],
                    "prompt_version": model_prompt_version(self.model),
                    "run_id": self.run_id,
                    "engine": getattr(self, "engine_version", None),
                    "requested_profile": self.requested_profile.metadata(),
                    "effective_profile": self.profile.metadata(),
                    "fallbacks": self.fallbacks,
                    "startup_seconds": round(self.startup_seconds, 3),
                    "requests": self.statistics,
                    "summary": inference_summary(self.statistics, dict(self.counters), wall),
                }
                self.summary = report["summary"]
                atomic_json(self.directory / f"inference-{self.run_id}.json", report)
                atomic_json(self.directory / "last-inference-metrics.json", report)

    def generate(self, data: dict) -> str:
        self.last_request = None
        preparation = time.monotonic()
        prompt, lines, mapping = build_prompt(self.model, data)
        messages = [{"role": "user", "content": prompt}]
        rendered = self.client.post(
            "/apply-template",
            json={
                "messages": messages,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        rendered.raise_for_status()
        tokenized = self.client.post("/tokenize", json={"content": rendered.json()["prompt"]})
        tokenized.raise_for_status()
        native = self.model == "asmr-qwen3.5-9b"
        grounded = native or self.model.startswith("index-translate")
        overhead = 48 if native or grounded else 28
        budget = min(
            CONFIG["output_tokens"],
            max(512, 128 + sum(len(r["text"]) * (3 if grounded else 2) + overhead for r in lines)),
        )
        input_tokens = len(tokenized.json()["tokens"])
        grammar = output_grammar(
            list(mapping),
            [(r["start"], r["end"]) for r in lines] if native else None,
            [r["text"] for r in lines] if grounded else None,
        )
        request_id = str(time.time_ns())
        completion = {
            "prompt": rendered.json()["prompt"],
            "temperature": CONFIG["temperature"],
            "repeat_penalty": CONFIG["repeat_penalty"],
            "n_predict": budget,
            "stream": False,
            "cache_prompt": True,
            "grammar": grammar,
        }
        request_file = self.directory / f"request-{request_id}.input.json"
        terminology = effective_glossary(data, [row["text"] for row in data["lines"]])
        prompt_sha256 = hashlib.sha256(completion["prompt"].encode("utf-8")).hexdigest()
        atomic_json(request_file, {
            "schema": 1, "request_id": request_id, "model": self.model,
            "revision": REGISTRY[self.model][1], "prompt_version": model_prompt_version(self.model),
            "profile": self.profile.metadata(), "instruction": prompt, "mapping": mapping,
            "input_tokens": input_tokens, "output_budget": budget, "prompt_sha256": prompt_sha256,
            "effective_glossary": terminology,
            "completion": completion,
        })
        if self.log:
            self.log.write("\nOwnsei translation request: " + json.dumps({
                "id": request_id, "model": self.model, "input_tokens": input_tokens,
                "output_budget": budget, "prompt_sha256": prompt_sha256,
                "audit": str(request_file),
                "effective_glossary": terminology,
            }, ensure_ascii=False) + "\n")
            self.log.flush()
        if input_tokens + budget + 64 > CONFIG["context"]:
            raise ValueError("Prompt exceeds context budget; split batch or shorten notes/glossary")
        start = time.monotonic()
        self.last_request = {
            "lines": len(lines),
            "profile": self.profile.metadata(),
            "preparation_seconds": round(start - preparation, 3),
            "validation": "pending",
            "request_id": request_id, "request_file": str(request_file),
            "prompt_sha256": prompt_sha256, "input_tokens": input_tokens, "output_budget": budget,
            "effective_glossary": terminology,
        }
        self.statistics.append(self.last_request)
        # Render the model's native chat template once, then constrain native
        # completion directly, avoiding the chat/reasoning adapter and the
        # ambiguous string-pattern conversion in the JSON schema converter.
        try:
            response = self.client.post(
                "/completion",
                json=completion,
            )
            response.raise_for_status()
        finally:
            self.last_request["seconds"] = round(time.monotonic() - start, 3)
        reply = response.json()
        self.last_request.update(
            {
                "usage": {
                    "prompt_tokens": reply.get("tokens_evaluated"),
                    "completion_tokens": reply.get("tokens_predicted"),
                },
                "timings": reply.get("timings", {}),
                "finish_reason": reply.get("stop_type"),
            }
        )
        raw = reply.get("content")
        if not isinstance(raw, str):
            raise ValueError("Translation response must contain text")
        (self.directory / f"request-{request_id}.reply.txt").write_text(raw or "", encoding="utf-8")
        if reply.get("stopped_limit") or reply.get("stop_type") == "limit" or not reply.get("stop", True):
            raise ValueError("Translation output incomplete/truncated")
        if native:
            raw = normalize_native_reply(raw, lines, grounded=True)
        elif grounded:
            raw = normalize_grounded_reply(raw, lines)
        found = parse_reply(raw, list(mapping))
        return json.dumps([{"id": mapping[i], "text": text} for i, text in found.items()], ensure_ascii=False)


def translate(store: Store, track_id: str, model: str, selected: list[str] | None, progress, *, engine=None, reference=None):
    if model not in GGUF:
        raise ValueError("Unsupported translation model")
    store.prepare_translation(track_id, model)
    cues = store.cues(track_id)
    if not cues:
        raise ValueError("No Japanese transcript")
    raw_settings = store.settings()
    settings = {**raw_settings, **pin_translation_settings(raw_settings)}
    track_name = store.track(track_id).name
    directory = store.path / "cache" / track_id / "translation"
    directory.mkdir(parents=True, exist_ok=True)
    selected = set(selected or [])
    selected_ordinals = {c.ordinal for c in cues if c.id in selected}
    if selected and len(selected_ordinals) != len(selected):
        raise ValueError("Selected cues are not in the active transcript")
    converter = converter_for(settings)
    preferences = translation_preferences(settings)
    batches = natural_batches(cues)

    def needs_update(c):
        previous = c.provenance.get("translation", {})
        return c.zh_override is None and (
            c.translation_status in ("pending", "stale", "failed")
            or not c.zh
            or polluted(c.zh_auto)
            or previous.get("model") != model
            or previous.get("revision") != REGISTRY[model][1]
            or previous.get("prompt_version") != model_prompt_version(model)
            or previous.get("config") != CONFIG
            or translation_preferences(previous) != preferences
        )

    targets = []
    for batch in batches:
        lo, hi = dependency_window(batch[0].ordinal)
        affected = any(lo <= ordinal < hi for ordinal in selected_ordinals)
        if (
            (affected and any(c.zh_override is None for c in batch))
            if selected
            else any(map(needs_update, batch))
        ):
            targets.append(batch)
    if not targets:
        return
    # Hide old exports before generating replacements, including model/prompt upgrades.
    for batch in targets:
        for c in batch:
            if c.zh_override is None:
                c.translation_status = "stale"
                store.save_generated(c, c.revision)
    nonce = store.translation_nonce(track_id, model)
    completed = 0
    total = sum(len(batch) for batch in targets)
    log = directory / "llama.log"
    if engine is None:
        progress({"log_source": str(log), "log_start": log.stat().st_size if log.exists() else 0,
                  "message": f"載入 {model} 翻譯模型", "stage": "translation"})
    else:
        progress({"message": "檢查翻譯批次與快取", "stage":"translation"})
    from contextlib import nullcontext
    with (nullcontext(engine) if engine is not None else Llama(model, directory)) as engine:

        def record(event, **details):
            if callable(callback := getattr(engine, "record", None)):
                callback(event, **details)

        def execution_metadata():
            callback = getattr(engine, "execution_metadata", None)
            return callback() if callable(callback) else None

        def process(batch):
            nonlocal completed
            data = payload(cues, batch, settings, track_name)
            from onsei.translation_reference import relevant_reference
            data["reference"] = relevant_reference(reference or [], batch)
            key = digest(
                {
                    "payload": data,
                    "model": REGISTRY[model][:2],
                    "config": CONFIG,
                    "prompt": model_prompt_version(model),
                    **({"generation": nonce} if nonce else {}),
                }
            )
            cache = directory / f"{key}.json"
            ids = [c.id for c in batch]
            translations = None
            execution = None
            if cache.exists():
                try:
                    cached = json.loads(cache.read_text())
                    translations = parse_reply(json.dumps(cached["lines"], ensure_ascii=False), ids)
                    validate_content(translations, data)
                    execution = cached.get("execution")
                    record("cache_hits")
                    record("cached_lines", lines=len(batch))
                except (ValueError, KeyError, TypeError):
                    translations = None
                    execution = None
                    cache.replace(cache.with_suffix(".invalid.json"))
                    progress({"message": "快取譯文格式異常，重新翻譯", "stage": "translation"})
            if translations is None:
                error = None
                for attempt in range(2):
                    try:
                        if attempt:
                            record("retries")
                        reply = engine.generate({**data, "retry": attempt > 0, "retry_error": str(error or "")})
                        (directory / f"{key}-{attempt}.reply.txt").write_text(reply, encoding="utf-8")
                        translations = parse_reply(reply, ids)
                        validate_content(translations, data)
                        record("accepted_requests")
                        execution = execution_metadata()
                        atomic_json(
                            cache,
                            {
                                "lines": [{"id": i, "text": t} for i, t in translations.items()],
                                "model": model,
                                "revision": REGISTRY[model][1],
                                "input": data,
                                "execution": execution,
                            },
                        )
                        break
                    except (ValueError, httpx.HTTPError) as e:
                        translations = None
                        error = e
                        if "context budget" in str(e) and len(batch) > 1:
                            record("splits")
                            middle = split_point(batch)
                            process(batch[:middle])
                            process(batch[middle:])
                            return
                        callback = getattr(engine, "reject", None)
                        recovered = callback(e) if callable(callback) else False
                        progress(
                            {
                                "message": f"第 {batch[0].ordinal + 1}–{batch[-1].ordinal + 1} 句"
                                f"翻譯驗證未通過（嘗試 {attempt + 1}/2）：{e}"
                                + ("；已切回一般推論" if recovered else ""),
                                "stage": "translation",
                            }
                        )
                        if "output incomplete/truncated" in str(e) and len(batch) > 1:
                            record("splits")
                            middle = split_point(batch)
                            process(batch[:middle])
                            process(batch[middle:])
                            return
                else:
                    if len(batch) > 1:
                        record("splits")
                        middle = split_point(batch)
                        process(batch[:middle])
                        process(batch[middle:])
                        return
                    c = batch[0]
                    if c.zh_override is None:
                        c.translation_status = "failed"
                        store.save_generated(c, c.revision)
                    raise RuntimeError(f"Translation failed at {c.id}: {error}") from error
            semantic_flags = validate_content(translations, data)
            for c in batch:
                if c.zh_override is not None:
                    continue
                c.zh_auto = converter.convert(translations[c.id])
                c.translation_status = "translated"
                c.provenance.pop("review", None)
                c.review_flags = [flag for flag in c.review_flags if not flag.startswith("translation_")] + semantic_flags[c.id]
                c.provenance["translation"] = {
                    "model": model,
                    **preferences,
                    "revision": REGISTRY[model][1],
                    "cache_key": key,
                    "prompt_version": model_prompt_version(model),
                    "config": dict(CONFIG),
                    "execution": execution,
                    "context_ids": [
                        n.id
                        for n in cues
                        if batch[0].ordinal - CONTEXT_CUES <= n.ordinal <= batch[-1].ordinal + CONTEXT_CUES
                    ],
                }
                if not store.save_generated(c, c.revision):
                    progress({"message": f"{c.id} 已被修改，略過舊推論結果"})
                else:
                    record("saved_lines", lines=1)
            completed += len(batch)
            progress(
                {
                    "message": f"已處理 {completed}/{total} 句（每批最多 {BATCH_SIZE} 句）",
                    "stage": "translation",
                    "fraction": completed / total,
                }
            )

        for batch in targets:
            process(batch)
        record("completed_tracks")
    summary = getattr(engine, "summary", None)
    if isinstance(summary, dict):
        progress(
            {
                "message": f"翻譯完成：{summary['wall_seconds']:.1f} 秒，"
                f"{summary['saved_lines_per_minute'] or 0:.1f} 句／分鐘；"
                f"重試 {summary.get('retries', 0)} 次，拆批 {summary.get('splits', 0)} 次，"
                f"回退 {len(engine.fallbacks)} 次",
                "stage": "translation",
                "inference_run": engine.run_id,
            }
        )
