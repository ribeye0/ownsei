# 第三方來源與模型授權資訊

Ownsei 程式碼採 [MIT](LICENSE)。模型權重不隨原始碼分發，使用各自上游授權。下表列出固定版本的模型卡標示，未標示者需查看來源授權。

| 本機選項 | 固定模型來源 | 模型卡標示 |
|---|---|---|
| `qwen3.5-9b-review` | [unsloth/Qwen3.5-9B-GGUF](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/tree/3885219b6810b007914f3a7950a8d1b469d598a5) | apache-2.0 |
| `whisper-ja-760m` | [efwkjn/faster-whisper-ja-760M](https://huggingface.co/efwkjn/faster-whisper-ja-760M/tree/341201ba3e542985d2ad3f78dafb9c1665535013) | 未標示，需查看來源授權 |
| `whisper-ja-anime-v0.3` | [efwkjn/whisper-ja-anime-v0.3](https://huggingface.co/efwkjn/whisper-ja-anime-v0.3/tree/77edc3f61a06161331b1803252f5987e433dd4a5) | 未標示，需查看來源授權 |
| `whisper-ja-1.5b` | [efwkjn/whisper-ja-1.5B](https://huggingface.co/efwkjn/whisper-ja-1.5B/tree/72c13fb7b6cc2a831780cdd79cd1a829e77bab02) | 未標示，需查看來源授權 |
| `cohere-asr-ja` | [efwkjn/cohere-asr-ja](https://huggingface.co/efwkjn/cohere-asr-ja/tree/8f1794e22b802731bdbf8ce53ff08f96a5af2bb4) | 未標示，需查看來源授權 |
| `aligner` | [Qwen/Qwen3-ForcedAligner-0.6B](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B/tree/c7cbfc2048c462b0d63a45797104fc9db3ad62b7) | apache-2.0 |
| `hy-mt2-7b` | [tencent/Hy-MT2-7B-GGUF](https://huggingface.co/tencent/Hy-MT2-7B-GGUF/tree/ab8472660ac61fac25f1af43fac2599d52a8a775) | apache-2.0 |
| `hy-mt2-1.8b` | [tencent/Hy-MT2-1.8B-GGUF](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/tree/a0c709d9fac510f2c807aa3af52872340dc37a4a) | apache-2.0 |
| `index-translate-9b` | [IndexTeam/Index-Translate-9B-GGUF](https://huggingface.co/IndexTeam/Index-Translate-9B-GGUF/tree/d01404384ba429b93f5f63d43573812d103b5bcd) | apache-2.0 |
| `index-translate-2b` | [IndexTeam/Index-Translate-2B-GGUF](https://huggingface.co/IndexTeam/Index-Translate-2B-GGUF/tree/449c9e6457b3632d328c6cbb78ae8e8e0c8059a5) | apache-2.0 |
| `asmr-qwen3.5-9b` | [mmis1000/asmr-qwen3.5-9b-zh-tw-gguf-v0.2](https://huggingface.co/mmis1000/asmr-qwen3.5-9b-zh-tw-gguf-v0.2/tree/9c40d4deaf8dbfdf32078fe3f85a409e074df72c) | apache-2.0 |

Cohere 載入程式另取自 [efwkjn/cohere-asr-ja-lite](https://huggingface.co/efwkjn/cohere-asr-ja-lite/tree/4c100172298efe8771f10ecccfee6e47a83e77cb)；模型權重及載入程式的來源分開固定與核對。

Catppuccin 配色來源採 MIT 授權，Copyright © 2021 Catppuccin；完整文字見 [Catppuccin-LICENSE](third_party/Catppuccin-LICENSE)，[上游授權](https://github.com/catppuccin/catppuccin/blob/main/LICENSE)。

主要 runtime： [llama.cpp](https://github.com/ggml-org/llama.cpp)、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[CTranslate2](https://github.com/OpenNMT/CTranslate2)、[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[OpenCC](https://github.com/BYVoid/OpenCC)、[FFmpeg](https://ffmpeg.org/legal.html)。它們分別由各自授權管理；Python、前端及模型 runtime 的完整依賴清單見 `uv.lock`、`frontend/package-lock.json`、`runtimes/*.lock`。

字型只引用使用者已安裝的 Hack Nerd Font、Hack、Cascadia Mono 與 Noto Sans 系列；不隨發布包分發字型。
