# 部署

## 主機

| 項目 | 條件 |
|---|---|
| 平台 | Linux amd64、glibc ≥ 2.38 |
| GPU | NVIDIA；`nvidia-smi` 可用，驅動 ≥ 570.124.06 |
| 系統庫 | libstdc++、libgomp、OpenSSL 3 |
| 工具 | FFmpeg／ffprobe、curl、tar、Node.js ≥ 22、npm |
| 資源 | 預設模型建議 12 GiB VRAM、16 GiB RAM、15 GiB 可用磁碟 |

Whisper 1.5B／Anime 首次 CPU 轉換建議 32 GiB RAM，轉換來源及可選 PyTorch 環境另占磁碟。較小顯存可選 Index 2B。已驗證 Arch Linux／RTX 4070 Ti；目前預編譯引擎不支援 musl、ARM64、Windows 或 AMD。

驅動門檻採 CUDA 12.8.1 的保守部署基準，見 [NVIDIA Release Notes](https://docs.nvidia.com/cuda/archive/12.8.1/cuda-toolkit-release-notes/index.html)。不需安裝完整 CUDA Toolkit。

## 安裝

在專案根目錄以一般帳號執行：

```sh
bash scripts/install.sh
bash scripts/start.sh --port 8765
```

安裝固定版本 uv／Python、鎖定主環境、校驗 llama.cpp b11541 CUDA 執行庫並建置前端。服務綁定 `127.0.0.1`。

分步執行：

```sh
bash scripts/setup.sh
.venv/bin/ownsei doctor --strict --prerequisites
bash scripts/setup-llama.sh
(cd frontend && npm ci && npm run build)
.venv/bin/ownsei doctor --strict
bash scripts/start.sh
```

## 模型

側欄「模型管理」提供下載、校驗、取消、重試、刪除及完整日誌。基本組合為 Whisper JA 760M＋Index Translate 9B；Cohere 會一併準備 ForcedAligner。Whisper 1.5B／Anime 下載原始權重後轉換為 CTranslate2 `int8_float16`。

```sh
.venv/bin/ownsei models list
.venv/bin/ownsei models prepare --model whisper-ja-760m
.venv/bin/ownsei models prepare --model index-translate-9b
# 使用 Cohere 時，CLI 需分別準備：
.venv/bin/ownsei models prepare --model aligner
.venv/bin/ownsei models prepare --model cohere-asr-ja
```

模型固定 revision，重試沿用下載快取。刪除移除 `models/<id>` 與對應轉換來源，保留共用 runtime 及作品。背景下載狀態保存於 `.runtime-envs/model-jobs/`。

檢查主機與已安裝的 CUDA runtime：

```sh
.venv/bin/ownsei doctor --strict --runtime-check
.venv/bin/ownsei doctor --json --runtime-check
```

## 更新與服務

停止任務及服務，備份 `workspace/`、外部作品目錄和原音，再更新原始碼並重跑 `install.sh`。保留 `models/`、`.tools/`、`.runtime-envs/`。Runtime 鎖檔更新後，在模型管理執行檢查／修復。

Systemd user service：修改 [ownsei.service.example](../deploy/ownsei.service.example) 的路徑，存為 `~/.config/systemd/user/ownsei.service`。

```sh
systemctl --user daemon-reload
systemctl --user enable --now ownsei
journalctl --user -u ownsei
```

環境檢查失敗先查 `doctor` 輸出；下載或轉換失敗查模型任務日誌。網路代理使用 `HTTP_PROXY`／`HTTPS_PROXY`。固定 Python wheels 可預取至 `.tools/wheels/`：

```sh
.venv/bin/python scripts/cache_wheels.py runtimes/whisper.lock
```
