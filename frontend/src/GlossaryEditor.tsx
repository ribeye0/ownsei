import {useState, useEffect} from "react";
import {t as ui} from "./i18n";
import {readEntries, type TermEntry} from "./terminology";
export function GlossaryEditor({value, onChange, disabled = false, scope}: {
    value: string; onChange: (value: string) => void; disabled?: boolean; scope: "project" | "global";
}) {
    const [source, setSource] = useState(false), [error, setError] = useState("");
    useEffect(() => setError(""), [value]);
    let rows: TermEntry[] = [], problem = "";
    try { rows = readEntries(value, false); readEntries(value); } catch(e) { problem = String(e); }
    function write(next: TermEntry[]) { onChange(JSON.stringify(next, null, 2)); setError(""); }
    function switchMode() {
        if (source) {
            try { write(readEntries(value)); } catch(e) { setError(String(e)); return; }
        }
        setSource(!source); setError("");
    }
    const heading = scope === "project" ? ui("作品術語表") : ui("全局術語表");
    return <section className="glossary-editor" aria-label={heading}>
      <div className="glossary-heading"><b>{heading}</b><button disabled={disabled} onClick={switchMode}>{source ? ui("兩欄編輯") : ui("JSON 原始碼")}</button></div>
      <p className="muted">{scope === "project" ? ui("勾選的作品術語優先於全局。同名列取消勾選，會在本作品停用該全局術語；刪除此列則恢復沿用全局。") : ui("跨所有作品共用；只將原文涉及的已啟用用詞提供給模型。中文會依目標語言轉換繁簡。")}</p>
      <p className="muted">{ui("日文術語支援平假名／片假名等價比對；漢字、不同詞形須另列。若分別設定不同譯名，優先使用與原文相同的字形。")}</p>
      {source ? <label>{ui("術語 JSON")}<textarea className="code" rows={10} disabled={disabled} value={value} onChange={e => {onChange(e.target.value); setError("");}}/></label>
      : <><div className="glossary-table"><div className="glossary-row glossary-columns"><span>{ui("啟用")}</span><span>{ui("日文")}</span><span>{ui("中文")}</span><span/></div>
          {rows.map((row, index) => <div key={index} className="glossary-row">
            <input aria-label={ui("啟用術語 ") + (index+1)} type="checkbox" checked={row.enabled} disabled={disabled} onChange={e => write(rows.map((r,i) => i === index ? {...r, enabled: e.target.checked} : r))}/>
            <input aria-label={ui("日文術語 ") + (index+1)} lang="ja" value={row.ja} disabled={disabled} onChange={e => write(rows.map((r,i) => i === index ? {...r, ja: e.target.value} : r))}/>
            <input aria-label={ui("中文術語 ") + (index+1)} value={row.zh} disabled={disabled} onChange={e => write(rows.map((r,i) => i === index ? {...r, zh: e.target.value} : r))}/>
            <button aria-label={ui("刪除術語 ") + (index+1)} disabled={disabled} onClick={() => write(rows.filter((_,i) => i !== index))}>×</button>
          </div>)}
        </div><button disabled={disabled} onClick={() => write([...rows, {ja:"", zh:"", enabled:true}])}>{ui("新增術語")}</button></>}
      {(error || problem) && <p className="error" role="alert">{ui("術語格式有誤：")}{ui((error || problem).replace(/^Error: /, ""))}</p>}
      <small>{ui("JSON 支援 [{\"ja\":\"日文\",\"zh\":\"中文\",\"enabled\":true}]；也可貼上舊版日中對照物件。停用會保留內容，空表表示不使用此處術語。")}</small>
    </section>;
}
