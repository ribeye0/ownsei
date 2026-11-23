import {t as ui} from "./i18n";
import { useEffect, useState } from "react";
import { api } from "./api";
import type { Track, TranscriptVersion, TranslationVersion } from "./types";
import { Modal } from "./Modal";
import { resultStamp } from "./resultInfo";
import { loadExportPreferences, saveExportPreferences } from "./exportPreferences";
type Sources = {
    versions: TranscriptVersion[];
    translations: TranslationVersion[];
};
type Delivery = {
    id: string | null;
    files: string[];
    has_zip: boolean;
    downloads: {index:number; name:string; path:string}[];
    tracks: {
        track_id: string;
        name?: string;
        files: string[];
        error: string | null;
        written: string[];
        write_errors: {path:string; error:string}[];
    }[];
};
export function ExportDialog({ base, tracks, current, models, onClose }: {
    base: string;
    tracks: Track[];
    current: string;
    models: Record<string, string>;
    onClose: () => void;
}) {
    const [defaults] = useState(loadExportPreferences);
    const [ids, setIds] = useState([current]), [languages, setLanguages] = useState(defaults.languages), [formats, setFormats] = useState(defaults.formats), [chinese, setChinese] = useState(defaults.chinese);
    const [languageTag, setLanguageTag] = useState(defaults.languageTag), [destination, setDestination] = useState(defaults.destination), [overwrite, setOverwrite] = useState(defaults.overwrite);
    const [saved, setSaved] = useState(false);
    useEffect(() => setSaved(false), [languages, formats, chinese, languageTag, destination, overwrite]);
    const [sources, setSources] = useState<Record<string, Sources>>({}), [selection, setSelection] = useState<Record<string, {
        version: string;
        result_id: string | null;
    }>>({});
    const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(""), [delivery, setDelivery] = useState<Delivery | null>(null);
    useEffect(() => {
        const controller = new AbortController();
        void Promise.all(tracks.map(async (t) => {
            const [versions, translations] = await Promise.all([api<TranscriptVersion[]>(`${base}/tracks/${t.id}/versions`, "GET", undefined, { signal: controller.signal }), api<TranslationVersion[]>(`${base}/tracks/${t.id}/translations`, "GET", undefined, { signal: controller.signal })]);
            return [t.id, { versions, translations }] as const;
        })).then(rows => { if (controller.signal.aborted)
            return; setSources(Object.fromEntries(rows)); setSelection(Object.fromEntries(rows.map(([id, v]) => [id, { version: tracks.find(t => t.id === id)?.active_version || "", result_id: v.translations.find(t => t.active)?.id || null }]))); })
            .catch(e => { if (!controller.signal.aborted)
            setError(String(e)); }).finally(() => { if (!controller.signal.aborted)
            setLoading(false); });
        return () => controller.abort();
    }, [base]);
    async function chooseVersion(id: string, version: string) {
        setBusy(true);
        setError("");
        setDelivery(null);
        try {
            const translations = await api<TranslationVersion[]>(`${base}/tracks/${id}/translations?version=${encodeURIComponent(version)}`);
            setSources(v => ({ ...v, [id]: { ...v[id], translations } }));
            setSelection(v => ({ ...v, [id]: { version, result_id: translations.find(t => t.active)?.id || translations[0]?.id || null } }));
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusy(false);
        }
    }
    function saveDefaults() {
        setError("");
        setSaved(false);
        try {
            saveExportPreferences({ languages, formats, chinese, languageTag, destination, overwrite });
            setSaved(true);
        } catch {
            setError(ui("無法保存匯出預設，請確認瀏覽器允許儲存網站資料。"));
        }
    }
    const eligible = tracks.filter(t => sources[t.id]?.versions.length);
    const selected = ids.filter(id => eligible.some(t => t.id === id));
    const toggle = (items: string[], value: string) => items.includes(value) ? items.filter(v => v !== value) : [...items, value];
    async function submit() {
        setBusy(true);
        setError("");
        setDelivery(null);
        try {
            setDelivery(await api<Delivery>(base + "/deliveries", "POST", { selections: selected.map(track_id => ({ track_id, ...selection[track_id] })), languages, formats, chinese,
                language_tag: languageTag, destination, overwrite: destination !== "download" && overwrite }, { timeoutMs: 120000 }));
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusy(false);
        }
    }
    return <Modal title={ui("字幕與逐字稿匯出")} onClose={onClose} busy={busy} className="export-dialog">
    <div className="batch-tools"><button disabled={busy || loading} onClick={() => { setIds(eligible.map(t => t.id)); setDelivery(null); }}>{ui("全選可匯出音軌")}</button><button disabled={busy} onClick={() => { setIds([]); setDelivery(null); }}>{ui("取消全選")}</button><span>{ui("已選 ")}{selected.length}{ui(" 條音軌")}</span></div>
    <div className="batch-tracks">{tracks.map(t => <div className="batch-track" key={t.id}>
      <label className="check"><input type="checkbox" disabled={busy || loading || !sources[t.id]?.versions.length} checked={selected.includes(t.id)} onChange={() => { setIds(toggle(ids, t.id)); setDelivery(null); }}/><span>{t.name}</span></label>
      {selected.includes(t.id) && <div className="export-sources"><label>{ui("日文來源")}<select aria-label={ui("日文來源")} value={selection[t.id]?.version || ""} disabled={busy} onChange={e => void chooseVersion(t.id, e.target.value)}>{sources[t.id]?.versions.map(v => <option key={v.version} value={v.version}>{models[v.model || ""] || ui("匯入")}{v.extra_alignment ? ui(" + 對齊") : ""} · {resultStamp(v.created_at, v.created_at_estimated)}</option>)}</select></label>
        <label>{ui("中文來源")}<select aria-label={ui("中文來源")} value={selection[t.id]?.result_id || ""} disabled={busy} onChange={e => { setSelection({ ...selection, [t.id]: { ...selection[t.id], result_id: e.target.value || null } }); setDelivery(null); }}><option value="">{ui("無中文結果")}</option>{sources[t.id]?.translations.map(v => <option key={v.id} value={v.id}>{models[v.model] || v.model} · {v.target_language === "zh-Hans" ? ui("简体") : ui("繁體")} · {resultStamp(v.created_at)}</option>)}</select></label></div>}
    </div>)}</div>
    <fieldset disabled={busy}><legend>{ui("語言")}</legend>{[["ja", ui("日文")], ["zh", ui("中文")], ["bilingual", ui("雙語（日文在上）")]].map(([id, label]) => <label className="check" key={id}><input type="checkbox" checked={languages.includes(id)} onChange={() => { setLanguages(toggle(languages, id)); setDelivery(null); }}/>{label}</label>)}</fieldset>
    <fieldset disabled={busy}><legend>{ui("格式")}</legend>{[["vtt", ui("VTT（預設）")], ["srt", "SRT"], ["lrc", "LRC"], ["ass", "ASS"], ["txt", "TXT"], ["jsonl", ui("JSONL（原始資料與診斷）")]].map(([id, label]) => <label className="check" key={id}><input type="checkbox" checked={formats.includes(id)} onChange={() => { setFormats(toggle(formats, id)); setDelivery(null); }}/>{label}</label>)}</fieldset>
    {formats.includes("lrc") && <small>{ui("LRC 僅記錄開始時間，不包含結束時間；雙語同列，以「 / 」分隔。")}</small>}
    <label>{ui("中文字體轉換")}<select value={chinese} disabled={busy} onChange={e => { setChinese(e.target.value); setDelivery(null); }}><option value="as-result">{ui("保留所選結果")}</option><option value="zh-Hant">{ui("轉成繁體中文")}</option><option value="zh-Hans">{ui("转成简体中文")}</option></select></label>
    <small>{ui("繁簡轉換不重跑模型，不修改原稿。JSONL 保留原始資料；缺譯或時間異常會列出原因，有效日文可獨立匯出")}</small>
    <label className="check"><input type="checkbox" disabled={busy} checked={languageTag} onChange={e => {setLanguageTag(e.target.checked); setDelivery(null);}}/>{ui("檔名加入語言標註（zh-tw／zh-cn；日文為 ja）")}</label>
    <small>{ui("預設完整保留原音檔檔名，只更換副檔名；多種語言或同名音軌以子資料夾區分，不修改檔名。")}</small>
    <label>{ui("匯出位置")}<select value={destination} disabled={busy} onChange={e => {setDestination(e.target.value); setDelivery(null);}}>
      <option value="download">{ui("提供下載（檔案位於頁面下方）")}</option><option value="source">{ui("同時寫入原音檔目錄")}</option><option value="sub">{ui("同時寫入原音檔目錄的 sub 子目錄")}</option>
    </select></label>
    {destination !== "download" && <>
      <div className="export-destinations">{tracks.filter(t => selected.includes(t.id)).map(t => <p key={t.id} className="code">{t.source.slice(0,t.source.lastIndexOf("/")+1)}{destination === "sub" ? "sub/" : ""}</p>)}</div>
      <label className="check"><input type="checkbox" disabled={busy} checked={overwrite} onChange={e => {setOverwrite(e.target.checked); setDelivery(null);}}/>{ui("覆寫已有的同名字幕檔案")}</label>
      <small>{overwrite ? ui("已啟用覆寫：同名檔案會被取代，包含目錄內已有的人工字幕。") : ui("預設保留已有的同名檔案；未寫入的項目仍可下載。")}</small>
    </>}
    <div className="batch-tools"><button type="button" disabled={busy || !languages.length || !formats.length} onClick={saveDefaults}>{ui("保存匯出設定為預設")}</button>{saved && <span role="status">{ui("已保存匯出預設")}</span>}</div>
    <small>{ui("下次開啟時沿用；音軌與結果版本需另選。設定保存在此瀏覽器。")}</small>
    {error && <p className="error" role="alert">{error}</p>}
    <button className="primary" disabled={busy || loading || !selected.length || !languages.length || !formats.length} onClick={() => void submit()}>{busy ? ui("準備匯出…") : ui("匯出")}</button>
    {delivery && <div className="delivery-result" role="status"><p>{ui("已產生 ")}{delivery.files.length}{ui(" 個檔案")}</p>
      {delivery.tracks.map(t => <div key={t.track_id}><p>{t.name || t.track_id}：{t.files.length}{ui(" 個檔案")}{t.error && <span className="error"> · {t.error}</span>}</p>
        {t.written.map(path => <p key={path} className="code">{ui("已寫入：")}{path}</p>)}
        {t.write_errors.map(item => <p key={item.path} className="error">{item.path} · {ui(item.error)}</p>)}
      </div>)}
      {delivery.id && <div className="file-list">{delivery.downloads.map(file => <a key={file.index} href={`/api${base}/deliveries/${delivery.id}/files/${file.index}`} download>{file.path}</a>)}</div>}
      {delivery.id && delivery.has_zip && <a className="download-button" href={`/api${base}/deliveries/${delivery.id}`} download>{ui("下載 ZIP（含匯出報告）")}</a>}
    </div>}
  </Modal>;
}
