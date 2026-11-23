"""Pinned generation profiles and per-task context options."""

from onsei.context import DOCUMENT_CONFIG, translation_options
from onsei.translation_contract import BATCH_SIZE, CONTEXT_CUES, MAX_SOURCE_CHARS

PROMPT_VERSION = 1
CONFIG = {
    "context": 8192,
    "output_tokens": 6144,
    "batch_size": BATCH_SIZE,
    "context_cues": CONTEXT_CUES,
    "max_batch_source_chars": MAX_SOURCE_CHARS,
    "temperature": 0,
    "repeat_penalty": 1.0,
    "enable_thinking": False,
    "protocol": "ordered-grounded-json",
    "profile_version": 1,
}


def model_prompt_version(model):
    return PROMPT_VERSION


def model_config(model, settings=None):
    if model == "qwen3.5-4b-names":
        return {**CONFIG, "context": 32768}
    options = translation_options(settings, model)
    return {**CONFIG, **DOCUMENT_CONFIG, "context": options["context"], "translation_options": options}
