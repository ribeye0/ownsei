<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="frontend/public/ownsei-logo-dark.png">
    <img src="frontend/public/ownsei-logo.png" alt="Ownsei" width="96">
  </picture>
</p>

# Ownsei — 日語音聲字幕工具

Ownsei 使用 Haas JA 將日語音聲轉錄成帶時間戳的逐字稿，再由 Index-NativeLong 參照前後文翻譯成中文，在指定譯文風格的同時保持長文用詞的一致性，改善前後人名或稱呼被翻譯成不同詞彙的問題。
轉錄與翻譯皆在本地執行，並透過 Vulkan 加速，支援 NVIDIA 、AMD 和 Intel 各家的顯卡（VRAM 需 8GB 以上）。

**[下載 Linux／Windows 版](https://github.com/ribeye0/ownsei/releases)**

## 從一段對話做出可以回聽的字幕

音聲作品裡，角色常省略主詞，也會在對話中交替使用名字、暱稱和敬稱。單看一句「黙っちゃった」，很難決定該譯成「怎麼不說話了？」還是「他沉默了」。這個選擇取決於前一句在問誰，以及眼前的人有沒有回答。Ownsei 把**帶時間戳的日文逐字稿**保留下來：翻譯模型能讀到來回對話，聽眾也能從一句中文跳回原音，自己判斷角色的語氣。

![Ownsei 架構圖。上半部由 speech.cpp 執行 Haas JA F16 轉錄日文，下半部由 llama.cpp 執行 Index-Nailong-9B 翻譯中文。](docs/architecture.svg)

**圖 1　轉錄與翻譯流程。** 實線沿著音軌、日文逐字稿、中文譯文到字幕匯出。紫色虛線是翻譯模型可以參照的資料，其中右側回路將前批譯文帶回下一批，讓先前採用的稱呼能沿用到後面的對話。[English diagram](docs/architecture.en.svg)

## 方法與模型

| 階段 | 預設做法 | 可以調整的地方 |
| --- | --- | --- |
| 語音切分 | FireRedVAD 在 CPU 上找出有人聲的區段，再送入轉錄模型。 | 耳語被切得太碎時可降低門檻，也可換成 Silero VAD 或使用固定時間窗。 |
| 日文轉錄 | speech.cpp 執行 Haas JA F16，使用模型輸出的時間戳組成逐字稿。 | 顯存較少時可選 Q8_0。辨識有誤時可先回聽、修正日文，再重譯該段。 |
| 中文翻譯 | llama.cpp 執行 Index-NativeLong 9B，分批讀入待譯句、前後日文與前批中文。預設上下文容量為 64K。 | 可選繁體或簡體，調整上下文容量（8K–256K）及譯文風格。常用詞可在全局共用，角色名與暱稱可在專案內指定。 |

**同一份逐字稿可以試不同譯風。** 除了 NativeLong，也可選 Index-Translate 9B，或以日語音聲台詞與人工中文字幕微調的 Ownsei SFT48。SFT48 著重於成人向音聲的用詞與字幕標點。這兩個模型預設搭配 Qwen3.5 4B 整理人物譯名，使用者指定的術語優先。每次翻譯獨立保存，可以切換版本，比較同一句話的不同譯法。

## 端到端實測

在 Linux Vulkan 發布包 **0.2.1** 上，以 **Radeon RX 9070 XT** 處理一段 22 秒的日語對話。這次使用固定時間窗、Haas JA F16 與 NativeLong 9B Q4_K_M，上下文設為 8K，關閉人物譯名記憶。

| 日文轉錄 | 中文翻譯 | 整個任務（含匯出） | 輸出 |
| ---: | ---: | ---: | --- |
| 0.74 秒 | 4.66 秒 | **5.57 秒** | 7 句有時間戳的日中雙語字幕 |

耗時取自這次任務的起訖紀錄，從已匯入的音軌開始計時。整部作品的翻譯速度還會受前文長度、批次數量及顯存影響。

### 與 ChickenRice 的字幕比較（待測）

同一段日語對話，直接從語音譯成中文，和先保留日文原稿、再參照上下文翻譯，在什麼情況下會有差別？對照組採用 ChickenRice 官方翻譯版，其 Faster Whisper 模型直接輸出中文。該專案另有日文轉錄版。[ChickenRice 模型與版本說明](https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice/blob/62104884d8553be1c30bc88c157d82b2b8d0792b/README.md)

兩套工具各自以預設流程處理同一條完整音軌，保留原始輸出。先選定評閱時段，再隱去工具名稱，按相同時間範圍回聽兩份字幕。重點看三件事：有沒有譯對「誰在對誰說話」、同一角色的名字和稱呼能否前後對應，以及耳語或短句是否漏譯、重複或憑空多出。兩邊都通順且忠於原意時可判平手。若作品附人工字幕，留到評閱時才拿來參照。

## 從整部作品找到一句台詞

匯入作品目錄後，可先挑一軌試譯，再勾選其餘音軌批量處理。同一音軌若同時有 WAV 與 MP3，預設只匯入 WAV。逐字稿並列顯示日文和中文，只記得譯文中的幾個字，也能搜尋後跳回那段原音。

匯出可選日文、中文或雙語，支援 VTT、SRT、LRC、ASS 與 JSONL。例如為 `01 プロローグ.wav` 匯出中文 VTT，預設檔名就是 `01 プロローグ.vtt`。字幕可直接存到音檔旁，或集中放在 `sub/`。

## 安裝

| 平台 | 發布檔 | 解壓後啟動 |
| --- | --- | --- |
| Windows 10／11 x64 | `ownsei-版本號-windows-x64-vulkan.zip` | 雙擊 `Ownsei.cmd` |
| Linux x86-64（glibc 2.38 以上） | `ownsei-版本號-linux-x64-vulkan.tar.gz` | 執行 `bash ownsei.sh` |

首次啟動會安裝 Python 環境並開啟瀏覽器。在「模型管理與環境檢查」按 **準備基本組合**，即可下載 FireRedVAD、Haas JA F16 與 NativeLong 9B。首次安裝與下載建議預留 12–15 GiB。Linux 另需 curl、FFmpeg 與 ffprobe。

升級時先關閉舊版，將 `workspace/`（專案）與 `models/`（模型）複製到新版目錄。專案依路徑引用音檔，原始音檔請留在原處。

## 開發

Python 3.12、FastAPI、React 與 TypeScript。Linux 從原始碼啟動另需 Node.js 與 npm。

```sh
bash ownsei.sh         # 安裝依賴並啟動
bash scripts/check.sh  # 執行檢查、測試與前端建置
```

## License

[MIT](LICENSE)
