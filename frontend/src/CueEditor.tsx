import {t as ui} from "./i18n";
import type { Cue } from "./types";
export function CueEditor({ cue, draft, busy, readOnly, changed, onChange, onSave, onClose, onReset }: {
    cue: Cue;
    draft: Cue;
    busy: boolean;
    readOnly: boolean;
    changed: boolean;
    onChange: (cue: Cue) => void;
    onSave: () => void;
    onClose: () => void;
    onReset: () => void;
}) {
    return <div className="cue-edit">
    {changed && <p role="alert">{ui("這句的保存結果已更新；草稿已保留。儲存時會檢查版本衝突，請先複製草稿並重新載入比對。")}</p>}
    <div><label>{ui("日文台詞")}<textarea readOnly={readOnly} aria-label={ui("日文台詞")} lang="ja" value={draft.ja} onChange={e => onChange({ ...draft, ja: e.target.value })}/></label>
      <details><summary>{ui("查看 ASR 原始紀錄")}</summary><p lang="ja">{cue.ja_raw}</p></details>
      {cue.provenance.alignment_diagnostic && <details><summary>{ui("時間對齊診斷")}</summary><pre>{JSON.stringify(cue.provenance.alignment_diagnostic, null, 2)}</pre></details>}
    </div>
    <label>{ui("中文台詞")}<textarea readOnly={readOnly} aria-label={ui("中文台詞")} value={draft.zh || ""} onChange={e => onChange({ ...draft, zh: e.target.value })}/></label>
    <div className="edit-actions">
      <label>{ui("開始（秒）")}<input readOnly={readOnly} type="number" step=".01" min="0" value={draft.start_ms / 1000} onChange={e => onChange({ ...draft, start_ms: Math.round(Number(e.target.value) * 1000) })}/></label>
      <label>{ui("結束（秒）")}<input readOnly={readOnly} type="number" step=".01" min="0" value={draft.end_ms / 1000} onChange={e => onChange({ ...draft, end_ms: Math.round(Number(e.target.value) * 1000) })}/></label>
      <label className="check"><input disabled={readOnly} type="checkbox" checked={draft.review_flags.includes("manual_review")} onChange={e => onChange({ ...draft, review_flags: e.target.checked ? [...draft.review_flags, "manual_review"] : draft.review_flags.filter(x => x !== "manual_review") })}/>{ui("需要回聽")}</label>
      <button className="primary" disabled={busy || readOnly} onClick={onSave}>{ui("儲存 · Ctrl ↵")}</button>
      <button disabled={busy} onClick={onClose}>{ui("收起")}</button>
      <button disabled={busy || readOnly} onClick={onReset}>{ui("恢復模型文字")}</button>
    </div>
  </div>;
}
