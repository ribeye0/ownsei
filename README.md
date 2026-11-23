# Ownsei

本機日語音聲轉錄、中文翻譯與字幕校訂工具。Python API + React 介面；Haas 使用 speech.cpp，翻譯及人物整理使用 llama.cpp。預設 Haas JA F16 → Index NativeLong 9B。

## 執行

目前實機驗收環境：Linux x86-64、Python 3.12、glibc ≥ 2.38。系統需 FFmpeg、Node.js／npm、Vulkan loader 與 GPU 驅動。檔案選擇器使用 KDialog／Zenity，也可直接填入路徑。

```sh
bash scripts/install.sh
.venv/bin/ownsei models prepare --model haas-ja-v1.0-f16
.venv/bin/ownsei models prepare --model firered-vad
.venv/bin/ownsei models prepare --model index-nativelong-9b
bash scripts/start.sh
```

介面：<http://127.0.0.1:8878/>。可在模型管理中下載基本組合。程式監聽 loopback；`workspace/` 保存專案、版本與任務，`models/` 保存權重。匯入以本機音檔路徑引用，字幕預設保留原音檔名稱。

```sh
.venv/bin/ownsei doctor --strict
.venv/bin/ownsei models list
.venv/bin/ownsei init workspace/demo --title demo
.venv/bin/ownsei import workspace/demo /path/to/audio
.venv/bin/ownsei run workspace/demo --context 32768
```

Haas 可選 F16／Q8_0、FireRedVAD／Silero／固定時間窗，時間戳取自原生 token。Whisper JA 760M／1.5B 為選用模型，使用 CT2 CPU INT8；CUDA 環境另以 `ownsei runtime whisper-cuda` 準備。翻譯可選 NativeLong、Index Translate、[SFT48 Q4_K_M](https://huggingface.co/yzk9/index-translate-asmr-9B)，上下文 8K–256K；人物譯名、全局／專案術語與風格指引均可配置。模型由模型管理介面下載。

支援單軌／批量處理、取消與恢復任務、雙語回聽及校訂、繁／簡字幕、VTT／SRT／LRC／ASS／JSONL 匯出、ZIP 與寫回音檔目錄。各次轉錄及翻譯獨立保存，人工校訂使用版本檢查避免覆寫。

## 結構

| 範圍 | 入口與責任 |
|---|---|
| 模型 | `catalog.py` 固定來源、版本、校驗與顯示資訊；`models.py` 準備權重與 runtime；`modelmanager.py` 管理下載任務 |
| 轉錄 | `asr.py` 組織轉錄；`segmentation.py` 切分語音；`speech_worker.py`／`worker.py` 隔離推理 |
| 翻譯 | `translation_pipeline.py` 管理一次任務；`translation.py` 分批與重試；`inference.py` 管理 llama.cpp；`translation_adapters.py` 組裝 prompt 與解析 |
| 資料 | `storage.py`／`versions.py`／`translation_runs.py` 保存專案與結果；`snapshots.py` 驗證來源版本 |
| 引擎 | `backend.py` 負責執行檔、函式庫環境與裝置選擇，與字幕處理邏輯分離 |
| 介面 | `api.py` 為專案操作入口，`api_system.py` 為環境與模型 API；`frontend/src/` 保存工作區與共用元件 |

speech.cpp 固定 v0.11.2，llama.cpp 固定 b11541，安裝腳本驗證發布檔 SHA256。Haas 使用 Vulkan／CPU；CUDA 發行配置僅切換 llama.cpp。提供 Linux／Windows x64 的 CUDA 與 Vulkan 發布包。Windows 11 已完成安裝、HTTP、原生檔案選擇器、Edge 操作與 AMD Vulkan 推理實測。

CLI 環境設定：`ONSEI_ROOT` 指定資料根目錄；`ONSEI_ASR_DEVICE`、`ONSEI_LLM_DEVICE` 指定 `auto`／`CPU`／對應 GPU ID；`ONSEI_VAD_ENGINE` 指定 `firered`／`silero`／`none`。可用 `ONSEI_SPEECH_BINARY`、`ONSEI_LLM_BINARY` 明確指定引擎。GUI 任務會保存自己的設定。

## 發布包

Release 包含建置好的網頁及 speech.cpp／llama.cpp；首次安裝下載 Python 3.12 與依賴，不需 Node.js。Linux 執行 `bash ownsei.sh`；Windows 執行 `Ownsei.cmd`，`Install.cmd` 可單獨安裝環境。Windows 缺少 FFmpeg 時由安裝腳本下載固定的 LGPL 版本。

CUDA 包的翻譯引擎使用 CUDA 13.4，需相容的 NVIDIA 驅動；Haas 仍使用 Vulkan。Vulkan 包適用 NVIDIA／AMD／Intel。模型由介面另行下載。二進位與授權清單見 `release.json`、`manifest.json`、`third_party/`。

發布包依 Linux／Windows 分開提供；各包含對應平台的引擎與啟動檔。啟動器在 8878 被占用時會自動選用其他連接埠並開啟瀏覽器；可用 `--port NUMBER` 指定連接埠。後端視窗顯示服務網址、警告與 HTTP 錯誤。需要查看每次請求時，執行 `bash ownsei.sh --verbose` 或 `Ownsei.cmd --verbose`。

基本模型權重約 6.9 GB，另需 VAD Python 執行環境與下載／安裝暫存；首次準備建議保留 12–15 GiB。準備前會按所在磁碟檢查預估容量，失敗後可重試續傳。

## 開發與打包

```sh
bash scripts/setup.sh
(cd frontend && npm ci)
bash scripts/check.sh
.venv/bin/python scripts/package_source.py --output artifacts/release
.venv/bin/python scripts/package_release.py --cache downloads --output artifacts/release/platforms
```

`check.sh` 執行靜態檢查、後端與前端測試、前端建置、原始碼檔案檢查；GitHub Actions 使用相同入口。前端開發伺服器以 `npm run dev` 啟動，API proxy 指向 8878。

Windows 開發環境在 `uv sync --locked --python 3.12` 與 `npm ci --prefix frontend` 後執行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`；另需 FFmpeg／ffprobe。結果與 JUnit 紀錄保存在 `artifacts/validation/`。已安裝的發布包可用其 `.venv` Python 執行 `scripts/smoke_release.py --release-root PATH --output NEW_DIRECTORY`，透過獨立資料目錄檢查 HTTP、匯入、校訂與匯出；原生視窗及 GPU 推理另行實機驗收。

原始碼包包含程式、測試、鎖檔、部署腳本、UI 素材與逐檔校驗清單；模型與使用資料獨立保存。

MIT。第三方來源見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
