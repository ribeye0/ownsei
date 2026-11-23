import { t as ui } from "./i18n";

// Historical labels keep imported results readable; model choices come from the backend catalog.
export function modelLabels(): Record<string, string> {
  return {
    "whisper-ja-760m": "Whisper 760M",
    "cohere-asr-ja": "Cohere",
    "whisper-ja-anime-v0.3": "Whisper Anime v0.3",
    "whisper-ja-1.5b": "Whisper 1.5B",
    "haas-ja-v1.0": "Haas Q8_0",
    "haas-ja-v1.0-f16": "Haas F16",
    "qwen3.5-9b-review": "Qwen3.5 9B",
    "index-translate-9b": "Index 9B",
    "index-translate-2b": "Index 2B",
    "index-nativelong-9b": "NativeLong 9B",
    "index-translate-ownsei-sft48": "Ownsei SFT48",
    "index-translate-ownsei-sft170": "Ownsei SFT170",
    "index-translate-ownsei-sft48-q4-k-m": "SFT48 Q4_K_M",
    "index-translate-ownsei-sft48-q5-k-m": "SFT48 Q5_K_M",
    "asmr-qwen3.5-9b": "ASMR Qwen 9B",
    "hy-mt2-7b": "Hy-MT2 7B",
    "hy-mt2-1.8b": "Hy-MT2 1.8B",
    legacy: ui("既有人工／機器譯文"),
  };
}
export function statusLabels(): Record<string, string> {
  return {
    queued: ui("排隊"),
    waiting: ui("等待 GPU"),
    running: ui("處理中"),
    completed: ui("完成"),
    cancelled: ui("已取消"),
    failed: ui("失敗"),
    interrupted: ui("已中斷"),
    stale: ui("待更新"),
    pending: ui("待翻譯"),
    translated: ui("已翻譯"),
    manual: ui("人工校對"),
    aligned: ui("已對齊"),
    asr: ui("ASR 初稿"),
    alignment_failed: ui("對齊失敗"),
  };
}
export function reviewLabels(): Record<string, string> {
  return {
    alignment_native_fallback: ui(
      "額外對齊結果不可靠，保留 ASR 原生時間，請回聽確認",
    ),
    alignment_zero_duration: ui("部分文字沒有獨立的有效時間，請回聽確認"),
    alignment_merged: ui("短句已與相鄰句段合併，保留原文與可定位的時間"),
    long_cue: ui("句段較長，請確認斷句"),
    low_asr_confidence: ui("辨識信心偏低，請回聽確認"),
    listen: ui("需要回聽"),
    translation_invalid_output: ui("機器譯文含格式碎片或模型標記，請重新翻譯"),
    translation_honorific_question: ui(
      "舊版稱謂字串提示（新結果已停用，不能據此判定人物關係）",
    ),
    translation_glossary_question: ui("術語可能需確認"),
    translation_name_question: ui(
      "譯名可能未沿用人物記憶，請確認（也可能是自然省略）",
    ),
    translation_residual_japanese: ui(
      "譯文可能含未翻譯日文（也可能是姓名或引用）",
    ),
    translation_repetition_question: ui("不同原文出現相同譯文，請確認對應"),
    asr_boundary_question: ui("ASR 交界可能穿過字詞，請回聽確認"),
    translation_review_issue: ui("全文審校留下待確認疑點"),
  };
}
