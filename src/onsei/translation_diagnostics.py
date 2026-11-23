"""Translation failure reasons and recovery messages; no semantic quality scoring."""

import json
import re

import httpx


class TranslationResponseError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def repeated_output(raw, source_lengths=None):
    """Inspect only translation values, including an unfinished final JSON string.

    High thresholds distinguish runaway generation from ordinary stammering or
    repeated sound effects. This diagnoses truncated replies, never rewrites text.
    """
    for index, match in enumerate(re.finditer(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)', raw), 1):
        try:
            text = json.loads('"' + match[1] + '"')
        except ValueError:
            continue
        for run in re.finditer(r"(.{1,16}?)\1{11,}", text, re.DOTALL):
            threshold = 64 if len(run[1]) == 1 else 96
            if source_lengths and index <= len(source_lengths):
                threshold = max(threshold, source_lengths[index - 1] * 4)
            if len(run[0]) >= threshold:
                return {"row_index": index, "repeated_chars": len(run[0]), "unit_chars": len(run[1])}
    return None


def incomplete_response(raw, reply, budget, source_lengths=None):
    limited = reply.get("stopped_limit") or reply.get("stop_type") == "limit"
    if not limited and reply.get("stop", True):
        return None
    details = {
        "output_budget": budget,
        "completion_tokens": reply.get("tokens_predicted"),
        "finish_reason": reply.get("stop_type"),
    }
    repetition = repeated_output(raw, source_lengths)
    return TranslationResponseError(
        "repetition" if repetition else "output_limit" if limited else "incomplete_output",
        "Translation output incomplete/truncated",
        **details,
        **(repetition or {}),
    )


def failure_reason(error):
    if isinstance(error, TranslationResponseError):
        return error.code, error.details
    if isinstance(error, httpx.TimeoutException):
        return "timeout", {}
    if isinstance(error, httpx.HTTPStatusError):
        return "http_status", {"http_status": error.response.status_code}
    if isinstance(error, httpx.HTTPError):
        return "connection", {}
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json", {}
    message = str(error)
    if "context budget" in message:
        return "context_limit", {}
    if "output incomplete/truncated" in message:
        return "incomplete_output", {}
    if message in {"Grounded translation source/ID mismatch", "Translation IDs/order do not match source"}:
        return "source_mismatch", {}
    if message in {
        "Grounded translation row count mismatch",
        "Missing, extra, duplicate or empty translation",
    }:
        return "line_mismatch", {}
    if message == "Translation contains model tags or serialized JSON fragments":
        return "polluted_output", {}
    if message in {
        "Unexpected translation envelope",
        "Unclosed translation envelope",
        "Reply must be a JSON array",
        "Unexpected translation fields",
        "Translation response must contain text",
    }:
        return "invalid_format", {}
    return "translation_error", {}


def failure_event(
    error,
    first,
    last,
    attempt,
    *,
    split=False,
    recovered=False,
    request=None,
    ordinals=None,
    isolate=False,
    skip=False,
):
    code, details = failure_reason(error)
    reasons = {
        "repetition": "模型輸出異常重複",
        "output_limit": "譯文尚未完成，已用完本次輸出 token 額度",
        "incomplete_output": "模型回應未完整結束",
        "context_limit": "輸入與預留輸出超過上下文容量",
        "timeout": "等待翻譯模型回應逾時",
        "http_status": "翻譯推理服務回傳錯誤",
        "connection": "翻譯推理服務連線失敗",
        "service_error": "翻譯推理服務回報生成錯誤",
        "invalid_json": "譯文 JSON 格式無法解析",
        "source_mismatch": "譯文編號、順序或附帶原文與待譯台詞不符",
        "line_mismatch": "譯文句數不符，或有缺漏、重複編號、空白譯文",
        "polluted_output": "譯文混入模型標記或 JSON 碎片",
        "invalid_format": "模型回傳的譯文格式不符",
        "translation_error": "翻譯請求發生錯誤",
    }
    reason = reasons[code]
    cue_ordinal = None
    if code == "repetition":
        index = details.get("row_index", 0) - 1
        if ordinals and 0 <= index < len(ordinals):
            cue_ordinal = ordinals[index]
        location = f"第 {cue_ordinal} 句，" if cue_ordinal is not None else ""
        reason += f"（{location}同一片段連續重複 {details['repeated_chars']} 字元）"
    if code == "http_status":
        reason += f"（HTTP {details['http_status']}）"
    if isinstance(details.get("completion_tokens"), int):
        reason += f"；已生成 {details['completion_tokens']}/{details['output_budget']} tokens"
    if skip:
        action, explanation = "skip", "已標為翻譯失敗，繼續處理其他台詞"
    elif isolate:
        action, explanation = "isolate", "將問題句單獨重試，其餘台詞另批處理；保留前後文"
    elif split:
        action, explanation = "split", "將此批拆小重新翻譯"
    elif attempt < 2:
        action, explanation = "retry", "將重試（第 2/2 次）"
    else:
        action, explanation = "failed", "兩次嘗試均未成功，此句翻譯失敗"
    if recovered:
        explanation = "已切回一般推論；" + explanation
    if details.get("early_stopped"):
        explanation = "已提早中止生成；" + explanation
    location = f"第 {first} 句" if first == last else f"第 {first}–{last} 句"
    event = {
        "message": f"{location}：{reason}；{explanation}",
        "stage": "translation",
        "level": "error" if action in {"failed", "skip"} else "warning",
        "code": code,
        "action": action,
        "attempt": attempt,
        "error": str(error),
        **details,
    }
    if cue_ordinal is not None:
        event["cue_ordinal"] = cue_ordinal
    if request and request.get("request_id"):
        event["request_id"] = request["request_id"]
    return event
