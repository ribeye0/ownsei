"""Pinned complete SFT48 inference packages."""

LOCAL_GGUFS = {
    "index-translate-ownsei-sft48-q4-k-m": {
        "step": 48,
        "quantization": "Q4_K_M",
        "filename": "Ownsei-Index-Translate-9B-SFT48.Q4_K_M.gguf",
        "sha256": "72aeb84450fa702f9d21efa4c427f9cc12e7b9c5a01a6120414444052dd80f97",
        "package": "Ownsei-Index-Translate-9B-SFT48-GGUF",
        "bytes": 5780090304,
    },
    "index-translate-ownsei-sft48-q5-k-m": {
        "step": 48,
        "quantization": "Q5_K_M",
        "filename": "Ownsei-Index-Translate-9B-SFT48.Q5_K_M.gguf",
        "sha256": "143e5886bfa8e5fd5d47b4fd4a5f2f1cc67ba5d93be99176955eb5dee37264fb",
        "package": "Ownsei-Index-Translate-9B-SFT48-GGUF",
        "bytes": 6642544064,
    },
}
LOCAL_MODELS = frozenset(LOCAL_GGUFS)
