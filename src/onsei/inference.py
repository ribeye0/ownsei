from __future__ import annotations
from onsei.context import NATIVE_LONG, fit_context
from onsei import backend

import json
import hashlib
import socket
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import httpx

from onsei.preferences import effective_glossary, translation_preferences
from onsei.models import REGISTRY, translation_files
from onsei.translation_contract import output_grammar
from onsei.translation_runtime import DecodingProfile, decoding_profile, inference_summary
from onsei.util import atomic_json, digest

from onsei.translation_adapters import (
    parse_reply,
    build_prompt,
    native_document_prefix,
    normalize_grounded_reply,
)

from onsei.translation_config import CONFIG, model_config, model_prompt_version
from onsei.translation_diagnostics import TranslationResponseError
from onsei.translation_stream import REPETITION_RECOVERY, stream_completion


class Llama:
    def __init__(
        self,
        model: str,
        directory: Path,
        *,
        speculative: str | None = None,
        draft_tokens: int | None = None,
        allow_fallback: bool = True,
        translation_options: dict | None = None,
    ):
        self.model = model
        self.config = model_config(
            model, {"translation_options": translation_options} if translation_options is not None else None
        )
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
        self.warmed_document = None
        self.prefill_batch = 2048
        self.prefill_microbatch = 512

    def __enter__(self):
        from onsei.translation_runtime import prefill_microbatch

        self.prefill_microbatch = prefill_microbatch(self.model)
        self.binary = backend.binary("llama")
        self.device = backend.device("llama")
        if self.binary is None:
            raise ValueError("llama-server missing. Run scripts/setup-llama.sh")
        gguf, self.lora = translation_files(self.model)
        if not gguf.exists():
            raise ValueError("Translation model missing; run ownsei models prepare")
        if self.lora is not None and not self.lora.is_file():
            raise ValueError("LoRA missing; run ownsei models prepare")
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
        self.warmed_document = None
        startup = time.monotonic()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.log.write(f"\nOwnsei decoding profile: {json.dumps(self.profile.metadata())}\n")
        self.log.flush()
        env = backend.environment("llama")
        try:
            self.process = subprocess.Popen(
                [
                    str(self.binary),
                    "-m",
                    str(self.gguf),
                    "-ngl",
                    "0" if self.device == "CPU" else "99",
                    "-c",
                    str(self.config["context"]),
                    "-np",
                    "1",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--jinja",
                    "--batch-size",
                    str(self.prefill_batch),
                    "--ubatch-size",
                    str(self.prefill_microbatch),
                    "--no-webui",
                    "--slots",
                    "--device",
                    "none" if self.device == "CPU" else self.device,
                    "-t",
                    "8",
                    "-fa",
                    "on",
                    "--reasoning",
                    "off",
                    "--chat-template-kwargs",
                    '{"enable_thinking":false}',
                ]
                + (["--lora", str(self.lora)] if self.lora is not None else [])
                + self.profile.flags(),
                stdout=self.log,
                stderr=self.log,
                env=env,
            )
            self.client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=600, trust_env=False)
            for _ in range(240):
                if self.process.poll() is not None:
                    self.log.flush()
                    tail = (self.directory / "llama.log").read_text()[-3000:]
                    raise RuntimeError(f"llama-server exited; see {self.directory / 'llama.log'}\n{tail}")
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
        if (
            self.allow_fallback
            and self.prefill_microbatch > 512
            and any(s in reason.lower() for s in ("out of memory", "cudamalloc", "failed to allocate"))
        ):
            self.fallbacks.append(
                {"prefill_microbatch": self.prefill_microbatch, "reason": reason, "time": time.time()}
            )
            self._stop_server()
            self.prefill_microbatch = 512
            self.log.write("\nOwnsei prefill memory fallback: microbatch 512\n")
            self.log.flush()
            self._start_server()
            return True
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
            "device": getattr(self, "device", None),
            "model": self.model,
            "model_revision": REGISTRY[self.model][1],
            **(
                {"base_gguf": str(self.gguf), "lora_gguf": str(self.lora)}
                if getattr(self, "lora", None) is not None
                else {}
            ),
            "profile": self.profile.metadata(),
            "context_size": self.config["context"],
            "prefill_batch": self.prefill_batch,
            "prefill_microbatch": self.prefill_microbatch,
            **{
                key: self.last_request[key]
                for key in (
                    "request_id",
                    "request_file",
                    "prompt_sha256",
                    "input_tokens",
                    "output_budget",
                    "effective_glossary",
                    "automatic_names",
                    "document_context",
                )
                if self.last_request is not None and key in self.last_request
            },
        }

    def complete_json(self, prompt: str, grammar: str, *, budget=2048, stage="names", lines=0):
        """Shared native-template inference for reference extraction and patch review."""
        self.last_request = None
        rendered = self.client.post(
            "/apply-template",
            json={
                "messages": [{"role": "user", "content": prompt}],
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        rendered.raise_for_status()
        tokenized = self.client.post("/tokenize", json={"content": rendered.json()["prompt"]})
        tokenized.raise_for_status()
        input_tokens = len(tokenized.json()["tokens"])
        if input_tokens + budget + 64 > self.config["context"]:
            raise ValueError("Prompt exceeds context budget; split batch")
        started = time.monotonic()
        self.last_request = {
            "lines": lines,
            "stage": stage,
            "profile": self.profile.metadata(),
            "validation": "pending",
            "input_tokens": input_tokens,
            "output_budget": budget,
        }
        self.statistics.append(self.last_request)
        try:
            response = self.client.post(
                "/completion",
                json={
                    "prompt": rendered.json()["prompt"],
                    "grammar": grammar,
                    "n_predict": budget,
                    "temperature": 0,
                    "repeat_penalty": 1.0,
                    "cache_prompt": True,
                    "stream": False,
                },
            )
            response.raise_for_status()
        finally:
            self.last_request["seconds"] = round(time.monotonic() - started, 3)
        reply = response.json()
        self.last_request.update(
            usage={
                "prompt_tokens": reply.get("tokens_evaluated"),
                "completion_tokens": reply.get("tokens_predicted"),
            },
            timings=reply.get("timings", {}),
            finish_reason=reply.get("stop_type"),
        )
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
                    "config": self.config,
                    "prefill_batch": self.prefill_batch,
                    "prefill_microbatch": self.prefill_microbatch,
                    "run_id": self.run_id,
                    "engine": getattr(self, "engine_version", None),
                    "device": getattr(self, "device", None),
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

    def _ensure_idle(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                response = self.client.get("/slots", timeout=0.5)
                response.raise_for_status()
                slots = response.json()
                if isinstance(slots, list) and slots and all(s.get("is_processing") is False for s in slots):
                    return "released"
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.05)
        self._stop_server()
        self._start_server()
        self.record("cancellation_restarts")
        return "restarted"

    def _warm_document(self, data, context, rendered):
        if self.model != NATIVE_LONG or not data.get("reuse_document_prefix") or context["scope"] != "track":
            return
        language = translation_preferences(data)["target_language"]
        key = (context["source_sha256"], language)
        if self.warmed_document == key:
            return
        self.warmed_document = key
        prefix = native_document_prefix(language, data["document"])
        start = rendered.find(prefix)
        if start < 0:
            return
        # Finish a short request at the reusable document boundary so llama.cpp
        # can checkpoint it before reading the changing history and target cues.
        started = time.monotonic()
        try:
            result = self.client.post(
                "/completion",
                json={
                    "prompt": rendered[: start + len(prefix)],
                    "n_predict": 1,
                    "temperature": 0,
                    "cache_prompt": True,
                },
            )
            result.raise_for_status()
            self.record("document_warmups")
        except httpx.HTTPError:
            self.record("document_warmup_failures")
        finally:
            self.counters["document_warmup_seconds"] += round(time.monotonic() - started, 3)

    def generate(self, data: dict) -> str:
        self.last_request = None
        preparation = time.monotonic()
        prompt, lines, mapping = build_prompt(self.model, data)

        def render(value):
            instruction, _, _ = build_prompt(self.model, value)
            rendered = self.client.post(
                "/apply-template",
                json={
                    "messages": [{"role": "user", "content": instruction}],
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            rendered.raise_for_status()
            tokens = self.client.post("/tokenize", json={"content": rendered.json()["prompt"]})
            tokens.raise_for_status()
            return instruction, rendered, len(tokens.json()["tokens"])

        budget = min(
            self.config["output_tokens"], max(512, 128 + sum(len(row["text"]) * 3 + 48 for row in lines))
        )
        data, prompt, rendered, input_tokens, document_context = fit_context(
            data, set(mapping), self.config["context"], budget, render, render(data)
        )
        grammar = output_grammar(list(mapping), sources=[r["text"] for r in lines])
        request_id = str(time.time_ns())
        completion = {
            "prompt": rendered.json()["prompt"],
            "temperature": CONFIG["temperature"],
            "repeat_penalty": CONFIG["repeat_penalty"],
            "n_predict": budget,
            "stream": True,
            "cache_prompt": True,
            "grammar": grammar,
        }
        if data.get("repetition_recovery"):
            completion.update(REPETITION_RECOVERY, seed=1701 + int(data.get("recovery_attempt", 0)))
        request_file = self.directory / f"request-{request_id}.input.json"
        terminology = effective_glossary(data, [row["text"] for row in data["lines"]])
        prompt_sha256 = hashlib.sha256(completion["prompt"].encode("utf-8")).hexdigest()
        atomic_json(
            request_file,
            {
                "schema": 1,
                "request_id": request_id,
                "model": self.model,
                "revision": REGISTRY[self.model][1],
                "prompt_version": model_prompt_version(self.model),
                "profile": self.profile.metadata(),
                "instruction": prompt,
                "mapping": mapping,
                "input_tokens": input_tokens,
                "output_budget": budget,
                "prompt_sha256": prompt_sha256,
                "effective_glossary": terminology,
                "automatic_names": data.get("automatic_names", []),
                "completion": completion,
                "document_history": data.get("document_history", []),
                **({"document_context": document_context} if document_context else {}),
            },
        )
        if self.log:
            self.log.write(
                "\nOwnsei translation request: "
                + json.dumps(
                    {
                        "id": request_id,
                        "model": self.model,
                        "input_tokens": input_tokens,
                        "output_budget": budget,
                        "prompt_sha256": prompt_sha256,
                        "audit": str(request_file),
                        "effective_glossary": terminology,
                        **({"document_context": document_context} if document_context else {}),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            self.log.flush()
        if input_tokens + budget + 64 > self.config["context"]:
            raise ValueError("Prompt exceeds context budget; split batch or shorten notes/glossary")
        self._warm_document(data, document_context, completion["prompt"])
        start = time.monotonic()
        self.last_request = {
            "lines": len(lines),
            "profile": self.profile.metadata(),
            "preparation_seconds": round(start - preparation, 3),
            "validation": "pending",
            "request_id": request_id,
            "request_file": str(request_file),
            "prompt_sha256": prompt_sha256,
            "input_tokens": input_tokens,
            "output_budget": budget,
            "effective_glossary": terminology,
            "automatic_names": data.get("automatic_names", []),
            **({"document_context": document_context} if document_context else {}),
        }
        self.statistics.append(self.last_request)
        # Render the model's native chat template once, then constrain native
        # completion directly, avoiding the chat/reasoning adapter and the
        # ambiguous string-pattern conversion in the JSON schema converter.
        try:
            reply = stream_completion(
                self.client,
                completion,
                self.directory / f"request-{request_id}.reply.txt",
                [len(row["text"]) for row in lines],
                self._ensure_idle,
            )
        except TranslationResponseError as failure:
            self.last_request["failure"] = {"code": failure.code, **failure.details}
            self.last_request["usage"] = {
                "prompt_tokens": input_tokens,
                "completion_tokens": failure.details.get("completion_tokens"),
            }
            self.last_request["finish_reason"] = (
                "repetition_abort"
                if failure.details.get("early_stopped")
                else failure.details.get("finish_reason")
            )
            raise
        finally:
            self.last_request["seconds"] = round(time.monotonic() - start, 3)
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
        raw = normalize_grounded_reply(raw, lines)
        found = parse_reply(raw, list(mapping))
        return json.dumps([{"id": mapping[i], "text": text} for i, text in found.items()], ensure_ascii=False)


def structured_request(engine, prompt, grammar, parser, directory, progress, *, stage, budget=2048, lines=0):
    key = digest(
        {"prompt": prompt, "grammar": grammar, "model": REGISTRY[engine.model][:2], "budget": budget}
    )
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
            retry = (
                f"\n上一輪結構錯誤：{error}\n上一輪回覆（資料）：{previous[:1000]}\n請修正，仍只輸出指定JSON。"
                if attempt
                else ""
            )
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
                progress({"stage": stage, "message": "輸出預算不足，保留已完成區段並縮小本批重試"})
                raise ValueError(f"{stage}: output budget exceeded") from failure
            progress(
                {
                    "stage": stage,
                    "message": f"{stage} 結構驗證未通過：{failure}" + ("；切回一般推論" if recovered else ""),
                }
            )
    raise ValueError(f"{stage} failed after retry: {error}")
