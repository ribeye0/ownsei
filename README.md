# Ownsei

本機日語音聲轉錄與中文字幕工具。ASR 與翻譯分開執行，保留日文原稿、翻譯版本及人工校對。

## 環境

Linux amd64、glibc ≥ 2.38、NVIDIA 驅動 ≥ 570.124.06。已在 Arch Linux／RTX 4070 Ti 12 GiB 驗證。

依賴：FFmpeg／ffprobe、curl、tar、Node.js ≥ 22、npm。安裝腳本準備 Python 3.12、uv 0.13.0 及 llama.cpp CUDA 12.8 執行庫，不需 CUDA Toolkit。

## 執行

```sh
bash scripts/install.sh
bash scripts/start.sh
```

介面：<http://127.0.0.1:8765>。從「模型管理」下載所需模型；安裝主程式不預載權重。

```sh
.venv/bin/ownsei doctor --strict --runtime-check
.venv/bin/ownsei --help
```

分步安裝、CLI 及 systemd 設定見 [部署](docs/deployment.md)。服務為本機單使用者設計。

## 模型與功能

| 階段 | 模型 |
|---|---|
| ASR | Whisper JA 760M（預設）、1.5B、Anime v0.3、Cohere ASR JA |
| 翻譯 | Index Translate 9B（預設）、2B、ASMR Qwen3.5 9B、Hy-MT2 1.8B／7B |
| 時間對齊 | Qwen3 ForcedAligner 0.6B；Whisper 可選，Cohere 必需 |

支援批量處理、繁簡字幕、自訂風格與術語、雙語播放及校對。匯出 VTT／SRT／ASS／TXT／Markdown／JSONL；可下載、打包或寫回音檔目錄。模型來源、revision 與量化設定固定於 [models.py](src/onsei/models.py)，授權見 [第三方資訊](THIRD_PARTY_NOTICES.md)。

## 結構

- `src/onsei/`：FastAPI、SQLite、任務排程、ASR 與翻譯。
- `frontend/`：React／TypeScript／Vite。
- `runtimes/`：各推論環境的獨立鎖檔。
- `workspace/`：作品資料庫、設定、快取與日誌；原音維持匯入路徑。
- `models/`、`.runtime-envs/`、`.tools/`：本機模型與執行環境，不納入版本庫。

## 開發

```sh
bash scripts/setup.sh
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
(cd frontend && npm ci && npm run test:player && npm run build)
```

版本為 `0.1.0`，CLI 名稱為 `ownsei`。更新前停止服務並備份作品資料，更新後重跑 `scripts/install.sh`。

授權：[MIT](LICENSE)。第三方模型與依賴使用各自授權。
