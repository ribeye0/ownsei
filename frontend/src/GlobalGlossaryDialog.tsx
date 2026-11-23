import {useEffect, useState} from "react";
import {api} from "./api";
import {Modal} from "./Modal";
import {GlossaryEditor} from "./GlossaryEditor";
import {readEntries, type TermEntry} from "./terminology";
import {t as ui} from "./i18n";
export function GlobalGlossaryDialog({defaults, onClose}: {defaults: TermEntry[]; onClose: () => void}) {
    const [value, setValue] = useState("[]"), [revision, setRevision] = useState(0);
    const [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(true), [error, setError] = useState("");
    async function load(signal?: AbortSignal) {
        setBusy(true);setError("");
        try {
            const result = await api<{revision:number; glossary_entries:TermEntry[]}>("/preferences/global", "GET", undefined, {signal});
            if (!signal?.aborted) {setValue(JSON.stringify(result.glossary_entries,null,2));setRevision(result.revision);setLoaded(true);}
        } catch(e) {if (!signal?.aborted) setError(String(e));}
        finally {if (!signal?.aborted) setBusy(false);}
    }
    useEffect(() => {
        const controller = new AbortController(); void load(controller.signal);
        return () => controller.abort();
    }, []);
    async function save() {
        setBusy(true);setError("");
        try {const entries=readEntries(value); await api("/preferences/global", "PUT", {revision, glossary_entries:entries}); onClose();}
        catch(e) {setError(String(e));} finally {setBusy(false);}
    }
    return <Modal title={ui("全局術語表")} onClose={onClose} busy={busy} className="glossary-dialog">
      {loaded ? <GlossaryEditor value={value} onChange={setValue} scope="global" disabled={busy}/> : <p>{busy ? ui("讀取設定中…") : ui("尚未載入設定")}</p>}
      <p>{ui("儲存後只用於新提交的翻譯任務；正在執行及續跑的任務、既有譯文不受影響。專案同名術語優先。")}</p>
      {error && <p className="error" role="alert">{ui(error.replace(/^Error: /,""))}</p>}
      <div className="manager-actions">
        <button disabled={busy} onClick={() => void load()}>{loaded ? ui("重新載入（捨棄此草稿）") : ui("重新載入")}</button>
        <button disabled={busy || !loaded || !defaults.length} onClick={() => setValue(JSON.stringify(defaults,null,2))}>{ui("載入預設術語至草稿")}</button>
        <button disabled={busy} onClick={onClose}>{ui("取消")}</button>
        <button className="primary" disabled={busy || !loaded} onClick={() => void save()}>{ui("儲存全局術語")}</button>
      </div>
    </Modal>;
}
