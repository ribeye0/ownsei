# 第三方來源

程式碼使用 MIT。模型權重使用各自的上游授權，固定版本與校驗值見 `src/onsei/catalog.py`。

| 選項 | 模型來源 | 模型卡標示 |
|---|---|---|
| `qwen3.5-4b-names` | [unsloth/Qwen3.5-4B-GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/tree/e87f176479d0855a907a41277aca2f8ee7a09523) | apache-2.0 |
| `whisper-ja-760m` | [efwkjn/faster-whisper-ja-760M](https://huggingface.co/efwkjn/faster-whisper-ja-760M/tree/341201ba3e542985d2ad3f78dafb9c1665535013) | 未標示，需查看來源授權 |
| `whisper-ja-1.5b` | [efwkjn/whisper-ja-1.5B](https://huggingface.co/efwkjn/whisper-ja-1.5B/tree/72c13fb7b6cc2a831780cdd79cd1a829e77bab02) | 未標示，需查看來源授權 |
| `haas-ja-v1.0` / `haas-ja-v1.0-f16` | [sakasegawa/Haas-ja-v1.0-GGUF](https://huggingface.co/sakasegawa/Haas-ja-v1.0-GGUF/tree/6b965d5007ba780585de51549d57a8c1c0d67c7e) · [上游 Haas](https://huggingface.co/third-intelligence/Haas-ja-v1.0) | 見模型卡與上游 Apache-2.0 |
| `index-translate-9b` | [IndexTeam/Index-Translate-9B-GGUF](https://huggingface.co/IndexTeam/Index-Translate-9B-GGUF/tree/d01404384ba429b93f5f63d43573812d103b5bcd) | apache-2.0 |
| `index-nativelong-9b` | [IndexTeam/Index-Nailong-9B-GGUF v1.1](https://huggingface.co/IndexTeam/Index-Nailong-9B-GGUF/tree/6a495efa273b70f41ac94ce649b1bdb2465dc520) | apache-2.0 |

Ownsei SFT48 完整 GGUF 基於 Index Translate 9B revision `4e06279e62352dab96bbeb6fa0579bca5e822d75`；模型發布包附有 Apache-2.0 與來源 NOTICE。

Haas 透過 [speech.cpp](https://github.com/nyosegawa/speech.cpp/tree/v0.11.2) 執行 GGUF。引擎發布版本與檔案校驗值見 `scripts/setup_engines.py`，權重不隨原始碼分發。VAD 使用 [FireRedVAD](https://github.com/FireRedTeam/FireRedVAD) 或 [Silero VAD GGUF](https://huggingface.co/sakasegawa/silero-vad-GGUF)，來源與固定版本見模型登錄表。

Catppuccin 配色來源採 MIT 授權，Copyright © 2021 Catppuccin；完整文字見 [Catppuccin-LICENSE](third_party/Catppuccin-LICENSE)，[上游授權](https://github.com/catppuccin/catppuccin/blob/main/LICENSE)。

主要 runtime： [llama.cpp](https://github.com/ggml-org/llama.cpp)、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[CTranslate2](https://github.com/OpenNMT/CTranslate2)、[OpenCC](https://github.com/BYVoid/OpenCC)、[FFmpeg](https://ffmpeg.org/legal.html)。它們分別由各自授權管理；Python、前端及模型 runtime 的完整依賴清單見 `uv.lock`、`frontend/package-lock.json`、`runtimes/*.lock`。
