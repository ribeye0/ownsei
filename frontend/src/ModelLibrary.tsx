import {useState} from "react";
import {api} from "./api";
import {t as ui} from "./i18n";

export type Library = {
  path: string; cache: string; default_path: string; free_bytes: number | null;
  legacy: boolean; unavailable: boolean;
};

export function ModelLibrary({library, used, size, busy, setBusy, onChange, onError}: {
  library: Library; used: number; size: (bytes:number)=>string; busy:boolean;
  setBusy:(value:boolean)=>void; onChange:(library:Library)=>void; onError:(error:string)=>void;
}) {
  const [editing,setEditing] = useState(false);
  const [path,setPath] = useState("");
  const [source,setSource] = useState("");
  const [mode,setMode] = useState<"select"|"move">("select");
  const [working,setWorking] = useState(false);

  async function pick() {
    setBusy(true); onError("");
    try {
      const choice = await api<{path:string|null}>("/native-picker","POST",{kind:"directory",initial:path},{timeoutMs:310000});
      if(choice.path) setPath(choice.path);
    } catch(e) { onError(String(e)); } finally { setBusy(false); }
  }
  async function apply() {
    setBusy(true); setWorking(true); onError("");
    try {
      const result = await api<Library & {leftovers:string[]}>("/models/library","POST",{path, mode, expected_source:source},{timeoutMs:24*60*60*1000});
      onChange(result); setEditing(false);
      if (result.leftovers.length) onError(ui("新模型庫已啟用，舊檔案仍在：")+result.leftovers.join(", "));
    } catch(e) { onError(String(e)); } finally { setWorking(false); setBusy(false); }
  }
  return <section className="model-library">
    <h3>{ui("模型庫")}</h3>
    <div className="library-path">{library.path}</div>
    <small>{ui("模型用量：")}{size(used)} · {ui("磁碟可用：")}{library.free_bytes===null?"—":size(library.free_bytes)}</small>
    <small className="library-path">{ui("下載快取：")}{library.cache}</small>
    {library.legacy && <small>{ui("正在沿用舊版模型庫")}</small>}
    {library.unavailable && <p role="alert">{ui("模型庫無法使用，請連接磁碟或更換位置")}</p>}
    {!editing ? <div className="manager-actions">
      <button disabled={busy || library.unavailable} onClick={()=>void api("/models/library/open","POST").catch(e=>onError(String(e)))}>{ui("開啟資料夾")}</button>
      <button disabled={busy} onClick={()=>{setPath(library.default_path);setSource(library.path);setMode(used && !library.unavailable?"move":"select");setEditing(true);onError("");}}>{ui("更改位置")}</button>
    </div> : <fieldset disabled={busy}>
      <legend>{ui("更改模型庫位置")}</legend>
      <label>{ui("目的資料夾")}<input value={path} onChange={e=>setPath(e.target.value)} /></label>
      <button onClick={pick}>{ui("選擇資料夾")}</button>
      <label><input type="radio" name="library-mode" checked={mode==="select"} onChange={()=>setMode("select")} />
        {ui("使用這個模型庫，保留原位置的檔案")}</label>
      <label><input type="radio" name="library-mode" disabled={library.unavailable} checked={mode==="move"} onChange={()=>setMode("move")} />
        {ui("搬移已安裝模型，校驗完成後切換")}</label>
      <div className="manager-actions">
        <button disabled={!path.trim() || path===library.path} onClick={apply}>{mode==="move"?ui("搬移並切換"):ui("使用此位置")}</button>
        <button onClick={()=>setEditing(false)}>{ui("取消")}</button>
      </div>
      {working && <p role="status">{mode==="move"?ui("正在搬移與校驗模型…"):ui("正在切換模型庫…")}</p>}
    </fieldset>}
  </section>;
}
