"""Native llama.cpp SSE completion with bounded runaway-generation recovery."""

import json

from onsei.translation_diagnostics import TranslationResponseError, incomplete_response, repeated_output


# Used only after a detected repetition failure, never on ordinary requests.
REPETITION_RECOVERY = {
    "temperature": 0.2,
    "top_p": 0.9,
    "top_k": 40,
    "dry_multiplier": 0.5,
    "dry_base": 1.75,
    "dry_allowed_length": 12,
    "dry_penalty_last_n": 256,
    "dry_sequence_breakers": ["\n", ":", '"', "*"],
}


def sse_events(lines):
    data = []
    for line in lines:
        if not line:
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield "\n".join(data)


def stream_completion(client, completion, reply_file, source_lengths, ensure_idle):
    raw, final, snapshot = "", None, {}
    checked_chars = 0
    error = None
    try:
        with reply_file.open("w", encoding="utf-8") as audit:
            with client.stream("POST", "/completion", json=completion) as response:
                response.raise_for_status()
                for event in sse_events(response.iter_lines()):
                    if event == "[DONE]":
                        break
                    item = json.loads(event)
                    if not isinstance(item, dict):
                        raise ValueError("Unexpected translation envelope")
                    if "error" in item:
                        raise TranslationResponseError("service_error", str(item["error"])[:500])
                    snapshot.update(item)
                    content = item.get("content", "")
                    if not isinstance(content, str):
                        raise ValueError("Translation response must contain text")
                    raw += content
                    audit.write(content)
                    if len(raw) - checked_chars >= 8 or item.get("stop") is True:
                        checked_chars = len(raw)
                        if repetition := repeated_output(raw, source_lengths):
                            raise TranslationResponseError(
                                "repetition",
                                "Translation output incomplete/truncated",
                                **repetition,
                                early_stopped=True,
                            )
                    if item.get("stop") is True:
                        final = item
                        break
        if final is None:
            raise TranslationResponseError("incomplete_output", "Translation output incomplete/truncated")
        if failure := incomplete_response(raw, final, completion["n_predict"], source_lengths):
            raise failure
        return {**final, "content": raw}
    except BaseException as caught:
        error = caught
        if isinstance(error, TranslationResponseError):
            error.details.update(
                output_budget=completion["n_predict"],
                completion_tokens=snapshot.get("tokens_predicted"),
                finish_reason=snapshot.get("stop_type"),
            )
        raise
    finally:
        # Closing the SSE response cancels llama.cpp's task. Confirm the sole
        # slot is released before any retry; restart our server if it is stuck.
        if final is None and not isinstance(error, (KeyboardInterrupt, InterruptedError)):
            cancellation = ensure_idle()
            if isinstance(error, TranslationResponseError):
                error.details["cancellation"] = cancellation
