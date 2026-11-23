"""Pinned model catalog. Static metadata only; no downloads or runtime imports."""

def vad_libraries(platform):
    suffix = "cp312-win_amd64.pyd" if platform == "windows" else "cpython-312-x86_64-linux-gnu.so"
    return ("ncnn/ncnn." + suffix, "_kaldi_native_fbank." + suffix)


FIRERED_BYTES = 1_187_804  # Offline graph, weights and two CMVN vectors

REGISTRY = {
    "firered-vad": ("lhwcv/FireRedVAD-NCNN-streaming", "8e9d2694e7e55abeda4bfdb14f31e56dc0d4c95e", "vad"),
    "qwen3.5-4b-names": ("unsloth/Qwen3.5-4B-GGUF", "e87f176479d0855a907a41277aca2f8ee7a09523", None),
    "haas-ja-v1.0": ("sakasegawa/Haas-ja-v1.0-GGUF", "6b965d5007ba780585de51549d57a8c1c0d67c7e", "speech"),
    "haas-ja-v1.0-f16": (
        "sakasegawa/Haas-ja-v1.0-GGUF",
        "6b965d5007ba780585de51549d57a8c1c0d67c7e",
        "speech",
    ),
    "silero-vad": ("sakasegawa/silero-vad-GGUF", "7fcb7aa2a70c312f8579fc987b3e1cdecf9321ca", "speech"),
    "index-translate-9b": (
        "IndexTeam/Index-Translate-9B-GGUF",
        "d01404384ba429b93f5f63d43573812d103b5bcd",
        None,
    ),
    "index-nativelong-9b": (
        "IndexTeam/Index-Nailong-9B-GGUF",
        "6a495efa273b70f41ac94ce649b1bdb2465dc520",
        None,
    ),
    "index-translate-ownsei-sft48-q4-k-m": (
        "yzk9/index-translate-asmr-9B",
        "260747b5571a480f9418b7f3d641ee6af75f34c8",
        None,
    ),
}
SPEECH_FILES = {
    "haas-ja-v1.0": (
        "Haas-616M-ja-v1.0-Q8_0.gguf",
        "cd5f60b2f1c411404b83fca4a65e3ce30a26d9e951b6a346529e65f22758afeb",
    ),
    "haas-ja-v1.0-f16": (
        "Haas-616M-ja-v1.0-F16.gguf",
        "f752e02f68c1dfff795900e40aa82e5302976fb5360080fcbd330d8b3c992cd8",
    ),
    "silero-vad": (
        "silero-vad-309K-v6.2-F32.gguf",
        "2fc312514e5fc4fa4ffd02b25c76a4853fa4130e8fb9777d01502183b01238cb",
    ),
}
ASR_MODELS = frozenset(("haas-ja-v1.0", "haas-ja-v1.0-f16"))

GGUF = {
    "qwen3.5-4b-names": "Qwen3.5-4B-Q4_K_M.gguf",
    "index-translate-9b": "Index-Translate-9B.Q4_K_M.gguf",
    "index-nativelong-9b": "Index-Nailong-9B.Q4_K_M.gguf",
    "index-translate-ownsei-sft48-q4-k-m": "Ownsei-Index-Translate-9B-SFT48.Q4_K_M.gguf",
}
GGUF_SHA256 = {
    "qwen3.5-4b-names": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
    "index-translate-9b": "9cc6758b0007f4cbf42ea3400768cbff91ae74ef8d640e467158348426d33713",
    "index-nativelong-9b": "8ae7b1b66f52a854a3bfb65fff30ffc6bbda6db071b3c4d98972c2a3e61525f7",
    "index-translate-ownsei-sft48-q4-k-m": "72aeb84450fa702f9d21efa4c427f9cc12e7b9c5a01a6120414444052dd80f97",
}
DEFAULT_ASR = "haas-ja-v1.0-f16"
DEFAULT_TRANSLATION = "index-nativelong-9b"
DEFAULT_NAMES = "qwen3.5-4b-names"

MODEL_INFO = {
    "silero-vad": ("Silero VAD v6.2", "vad", "309K", "GGUF F32 · CPU", 0.0013, "CPU；不佔顯存"),
    "firered-vad": ("FireRedVAD（Haas 台詞篩選）", "vad", "0.59M", "ncnn CPU", FIRERED_BYTES / 10**9, "CPU；不佔顯存"),
    "qwen3.5-4b-names": (
        "Qwen3.5 4B（人物譯名）",
        "names",
        "4B",
        "GGUF Q4_K_M · 32K context",
        2.741,
        "約 4–8 GiB；與翻譯模型交替載入",
    ),
    "haas-ja-v1.0": (
        "Haas JA Q8_0 · speech.cpp",
        "asr",
        "616M",
        "GGUF Q8_0 · Vulkan / CPU · 原生時間戳",
        0.66,
        "約 1–3 GiB",
    ),
    "haas-ja-v1.0-f16": (
        "Haas JA F16 · speech.cpp",
        "asr",
        "616M",
        "GGUF F16 · Vulkan / CPU · 原生時間戳",
        1.23,
        "約 2–4 GiB",
    ),
    "index-translate-9b": ("Index Translate 9B", "translation", "9B", "GGUF Q4_K_M", 5.8, "約 6–9 GiB"),
    "index-nativelong-9b": (
        "Index NativeLong 9B v1.1",
        "translation",
        "9B",
        "GGUF Q4_K_M · 64K context",
        5.63,
        "64K 已實測；上下文可選 8K–256K，顯存需求隨容量增加",
    ),
    "index-translate-ownsei-sft48-q4-k-m": (
        "Ownsei SFT48 · Q4_K_M（完整 GGUF）",
        "translation",
        "9B",
        "GGUF Q4_K_M · SFT 已合併",
        5.780090304,
        "約 6–9 GiB",
    ),
}

TRANSLATION_MODELS = frozenset(GGUF) - {DEFAULT_NAMES}
