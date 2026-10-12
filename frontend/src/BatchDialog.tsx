import {t as ui} from "./i18n";
import { useEffect, useState } from "react";
import { api } from "./api";
import type { Track, TranscriptVersion } from "./types";
import { Modal } from "./Modal";
import { TranslationPreferencesFields, type TranslationPreferences, type StyleVariants } from "./TranslationPreferences";
import { resultStamp } from "./resultInfo";
export function BatchDialog({ base, tracks, current, asr, translation, extraAlignment, preferences, defaultStyles, models, onClose, onSubmitted }: {
    base: string;
    tracks: Track[];
    current: string;
    asr: string;
    translation: string;
    extraAlignment: boolean;
    preferences: TranslationPreferences;
    defaultStyles: StyleVariants;
    models: Record<string, string>;
    onClose: () => void;
    onSubmitted: () => Promise<void>;
}) {
    const [ids, setIds] = useState<string[]>(current ? [current] : []), [mode, setMode] = useState("run");
    const [asrModel, setAsr] = useState(asr), [mt, setMt] = useState(translation), [align, setAlign] = useState(extraAlignment);
    const [prefs, setPrefs] = useState(preferences), [versions, setVersions] = useState<Record<string, TranscriptVersion[]>>({}), [sources, setSources] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState("");
    useEffect(() => {
        const controller = new AbortController();
        void Promise.all(tracks.map(async (track) => [track.id, await api<TranscriptVersion[]>(`${base}/tracks/${track.id}/versions`, "GET", undefined, { signal: controller.signal })] as const))
            .then(rows => { if (!controller.signal.aborted) {
            setVersions(Object.fromEntries(rows));
            setSources(Object.fromEntries(tracks.map(t => [t.id, t.active_version || ""])));
        } })
            .catch(e => { if (!controller.signal.aborted)
            setError(String(e)); }).finally(() => { if (!controller.signal.aborted)
            setLoading(false); });
        return () => controller.abort();
    }, [base]);
    const eligible = tracks.filter(t => mode !== "translate" || sources[t.id]);
    const selected = ids.filter(id => eligible.some(t => t.id === id));
    async function submit() {
        setBusy(true);
        setError("");
        try {
            await api(base + "/jobs", "POST", { action: mode, track_ids: selected, asr: asrModel, translation: mt, extra_alignment: align,
                continue_on_error: true, fresh_asr: mode!=="translate", after_review: false, use_reference: false, target_language: prefs.target_language, translation_style: prefs.translation_style,
                ...(mode === "translate" ? { transcript_versions: Object.fromEntries(selected.map(id => [id, sources[id]])) } : {}) });
            await onSubmitted();
            onClose();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusy(false);
        }
    }
    return <Modal title={ui("批量處理")} onClose={onClose} busy={busy} className="batch-dialog">
    <label>{ui("處理模式")}<select disabled={busy} value={mode} onChange={e => setMode(e.target.value)}>
      <option value="run">{ui("轉錄＋翻譯（建立新結果）")}</option><option value="transcribe">{ui("只轉錄（建立新逐字稿）")}</option><option value="translate">{ui("只翻譯（使用既有轉錄）")}</option>
    </select></label>
    <div className="batch-tools"><button disabled={busy || loading} onClick={() => setIds(eligible.map(t => t.id))}>{ui("全選")}</button><button disabled={busy} onClick={() => setIds([])}>{ui("取消全選")}</button><span>{ui("已選 ")}{selected.length}/{tracks.length}{ui(" 條音軌")}</span></div>
    <div className="batch-tracks">{tracks.map(track => <div className="batch-track" key={track.id}>
      <label className="check"><input type="checkbox" disabled={busy || (mode === "translate" && !sources[track.id])} checked={selected.includes(track.id)} onChange={e => setIds(e.target.checked ? [...ids, track.id] : ids.filter(id => id !== track.id))}/><span>{track.name}</span></label>
      {mode === "translate" && <label>{ui("使用轉錄")}<select disabled={busy || loading || !versions[track.id]?.length} value={sources[track.id] || ""} onChange={e => setSources({ ...sources, [track.id]: e.target.value })}>
        {!sources[track.id] && <option value="">{ui("尚無轉錄，請先轉錄")}</option>}
        {(versions[track.id] || []).map(v => <option key={v.version} value={v.version}>{models[v.model || ""] || ui("匯入")}{v.extra_alignment ? ui(" + 對齊") : ""} · {resultStamp(v.created_at, v.created_at_estimated)}</option>)}
      </select></label>}
    </div>)}</div>
    {mode !== "translate" && <><label>{ui("轉錄模型")}<select value={asrModel} disabled={busy} onChange={e => setAsr(e.target.value)}>{["whisper-ja-760m", "whisper-ja-1.5b", "whisper-ja-anime-v0.3", "cohere-asr-ja"].map(id => <option key={id} value={id}>{models[id]}</option>)}</select></label>
      <label className="check"><input type="checkbox" checked={align} disabled={busy || asrModel === "cohere-asr-ja"} onChange={e => setAlign(e.target.checked)}/>{ui("額外使用 Qwen3 ForcedAligner 進行對齊")}</label><small>{ui("Cohere 必須使用對齊；Whisper 預設使用原生時間")}</small></>}
    {mode !== "transcribe" && <><label>{ui("翻譯模型")}<select value={mt} disabled={busy} onChange={e => setMt(e.target.value)}>{["index-translate-9b", "asmr-qwen3.5-9b", "index-translate-2b", "hy-mt2-1.8b", "hy-mt2-7b"].map(id => <option key={id} value={id}>{models[id]}</option>)}</select></label>
      <TranslationPreferencesFields value={prefs} onChange={setPrefs} defaultStyles={defaultStyles} disabled={busy} compact/></>}
    <p>{ui("依音軌順序處理；單軌內容失敗會繼續其他音軌，模型或環境錯誤則停止。此處風格只用於本次任務，角色備註與術語表沿用作品設定")}</p>
    {error && <p className="error" role="alert">{error}</p>}
    <button className="primary" disabled={busy || loading || !selected.length || !prefs.translation_style.trim()} onClick={() => void submit()}>{busy ? ui("提交中…") : loading ? ui("讀取來源…") : ui("開始處理 ") + (selected.length) + ui(" 條音軌")}</button>
  </Modal>;
}
