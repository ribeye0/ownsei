import {useEffect, useState} from "react";
import {api} from "./api";
import {Modal} from "./Modal";
import {t as ui} from "./i18n";
type Job = {status:string; stage:string; message:string; error:string|null; bytes_done:number; bytes_total:number; transfer_bytes_done?:number; files_done:number; files_total:number};
export type ManagedModel = {id:string; name:string; role:string; parameters:string; format:string; basic:boolean; repo:string; revision:string; url:string|null; license_url:string|null; local?:boolean; local_kind?:"gguf"|"lora"|null; download_bytes_estimate:number; vram_estimate:string; installed:boolean; runtime_ready:boolean; ready:boolean; dependencies:string[]; bytes:number; source_bytes:number; job:Job|null};
type Environment = {ready:boolean; checks:{name:string;ok:boolean;detail:string;required:boolean}[]};
const active = (job:Job|null) => !!job && ["queued","running"].includes(job.status);
function size(value:number) {const units=["B","KiB","MiB","GiB"];const index=value>0?Math.min(3,Math.floor(Math.log(value)/Math.log(1024))):0;return (value/1024**index).toFixed(index?2:0)+" "+units[index];}
function status(model:ManagedModel) {
    if (active(model.job)) return model.job!.status === "queued" ? ui("等待中") : ui("準備中");
    return model.ready ? ui("可用") : model.installed ? ui("已下載；需要檢查／修復") : model.bytes || model.source_bytes ? ui("部分檔案；可續傳") : ui("未下載");
}
export function ModelManager({onClose,onChanged}:{onClose:()=>void;onChanged:()=>void}) {
    const [models,setModels]=useState<ManagedModel[]>([]),[selected,setSelected]=useState<string[]>([]);
    const [environment,setEnvironment]=useState<Environment|null>(null),[error,setError]=useState("");
    const [busy,setBusy]=useState(false),[deleting,setDeleting]=useState<ManagedModel|null>(null);
    const [logModel,setLogModel]=useState(""),[log,setLog]=useState("");
    const basic = models.filter(m=>m.basic&&!active(m.job)).map(m=>m.id);
    useEffect(()=>{
        const controller=new AbortController(); let pending=false;
        async function load() {
            if (pending) return; pending=true;
            try {const data=await api<{models:ManagedModel[]}>("/models","GET",undefined,{signal:controller.signal});if(!controller.signal.aborted)setModels(data.models);}
            catch(e){if(!controller.signal.aborted)setError(String(e));} finally{pending=false;}
        }
        void load(); const timer=setInterval(()=>void load(),1500);
        void api<Environment>("/system","GET",undefined,{signal:controller.signal,timeoutMs:30000}).then(value=>{if(!controller.signal.aborted)setEnvironment(value);}).catch(e=>{if(!controller.signal.aborted)setError(String(e));});
        return()=>{controller.abort();clearInterval(timer);onChanged();};
    },[]);
    useEffect(()=>{
        if(!logModel)return;
        const controller=new AbortController();let pending=false;
        async function load(){if(pending)return;pending=true;try{const value=await api<{text:string}>(`/models/${logModel}/log`,"GET",undefined,{signal:controller.signal});if(!controller.signal.aborted)setLog(value.text);}catch(e){if(!controller.signal.aborted)setError(String(e));}finally{pending=false;}}
        setLog("");void load();const timer=setInterval(()=>void load(),2000);
        return()=>{controller.abort();clearInterval(timer);};
    },[logModel]);
    async function download(ids:string[]) {
        setBusy(true);setError("");
        try {await api("/models/download","POST",{models:ids});const data=await api<{models:ManagedModel[]}>("/models");setModels(data.models);}
        catch(e){setError(String(e));}finally{setBusy(false);}
    }
    async function remove() {
        if(!deleting)return;setBusy(true);setError("");
        try {await api(`/models/${deleting.id}`,"DELETE");setDeleting(null);setModels((await api<{models:ManagedModel[]}>("/models")).models);onChanged();}
        catch(e){setError(String(e));}finally{setBusy(false);}
    }
    async function cancel(id:string) {
        setBusy(true);setError("");try{await api(`/models/${id}/cancel`,"POST");setModels((await api<{models:ManagedModel[]}>("/models")).models);}catch(e){setError(String(e));}finally{setBusy(false);}
    }
    return <Modal title={ui("模型管理與環境檢查")} onClose={onClose} busy={busy} className="model-manager">
      <p>{ui("先準備一個轉錄模型與一個翻譯模型即可開始。建議基本組合為 Haas＋NativeLong；其他模型依需要下載。")}</p>
      <div className="manager-actions"><button disabled={busy||!basic.length} onClick={()=>void download(basic)}>{ui("準備基本組合")}</button>
        <button disabled={busy||!selected.length} onClick={()=>void download(selected)}>{ui("準備勾選的模型")}</button></div>
      <small>{ui("下載、環境安裝、校驗及 CPU 轉換分開顯示。關閉視窗會繼續；取消或中斷後可重試續傳。下載不佔用 GPU，安裝共用環境會等待處理任務。大小與顯存僅供估算。")}</small>
      <details className="environment-check"><summary>{environment ? environment.ready ? ui("環境檢查：通過") : ui("環境檢查：有項目需要處理") : ui("正在檢查部署環境…")}</summary>
        {environment?.checks.map(check=><p key={check.name} className={check.required&&!check.ok?"error":"code"}>[{check.ok?"OK":check.required?"ERROR":"INFO"}] {check.name}：{check.detail}</p>)}
        <small>{ui("缺少引擎或系統依賴時，請參考 README.md，執行 bash scripts/install.sh；不用預先下載所有模型。")}</small>
      </details>
      {error&&<p className="error" role="alert">{error}</p>}
      {deleting&&<div className="model-delete-confirm" role="alertdialog" aria-label={ui("確認刪除模型")}><p>{ui("確定刪除模型？")}{deleting.name}</p>
        <p>{ui(deleting.local?"會刪除已安裝的 GGUF 與本機來源暫存；共用基底、專案及原始發布包保留。之後需重新匯入發布包。":"會刪除模型權重與此模型的下載／轉換暫存；專案、字幕及共用執行環境保留，之後可重新下載。處理或準備任務進行中會阻止刪除。")}</p>
        <button disabled={busy} onClick={()=>setDeleting(null)}>{ui("取消")}</button><button className="danger" disabled={busy} onClick={()=>void remove()}>{ui("確認刪除模型")}</button>
      </div>}
      <div className="model-list">{models.map(model=><section key={model.id} className="model-card">
        <div className="model-title"><label className="check"><input aria-label={ui("選擇模型 ")+model.name} type="checkbox" disabled={busy||active(model.job)} checked={selected.includes(model.id)} onChange={e=>setSelected(e.target.checked?[...selected,model.id]:selected.filter(id=>id!==model.id))}/><b>{ui(model.name)}</b></label><span>{model.basic?ui("建議基本模型"):ui("可選模型")} · {status(model)}</span></div>
        <p>{ui(({asr:"轉錄",translation:"翻譯",alignment:"時間對齊",vad:"語音活動偵測",names:"人物譯名",review:"實驗性審校"})[model.role as "asr"]||model.role)} · {ui(model.parameters)} · {ui(model.format)}</p>
        <p>{ui("下載來源約 ")}{size(model.download_bytes_estimate)} · {ui("顯存估算：")}{ui(model.vram_estimate)} · {ui("本機檔案：")}{size(model.bytes+model.source_bytes)}</p>
        {model.dependencies.length>0&&<small>{ui("會一併準備必要依賴：")}{model.dependencies.join(", ")}</small>}
        <p className="code">{model.url?<a href={model.url} target="_blank" rel="noreferrer">{model.repo}</a>:model.repo} · {model.revision.slice(0,12)}{model.license_url&&<> · <a href={model.license_url} target="_blank" rel="noreferrer">{ui("模型說明與授權")}</a></>}</p>
        {model.local&&<small>{model.local_kind==="gguf"?ui("本機完整 GGUF，尚無線上下載。準備按鈕會校驗並使用本機發布檔案；刪除模型不會刪除 artifacts 內的發布包。"):ui("本機 LoRA 發布包，尚無線上下載。首次安裝請執行 ownsei models install-adapter /path/to/package；準備按鈕可沿用本機來源。")}</small>}
        {model.job&&<div className="model-progress"><p>{ui(model.job.message)} {model.job.error&&<span className="error">{model.job.error}</span>}</p>
          {active(model.job)&&<>
            {model.job.stage==="download"?<>
              <progress aria-label={ui("模型檔案準備進度")} max={model.job.bytes_total||undefined} value={model.job.bytes_total>0&&model.job.bytes_done>0?Math.min(model.job.bytes_done,model.job.bytes_total):undefined}/>
              {model.job.bytes_total>0&&<span>{ui("檔案寫入：")}{size(model.job.bytes_done)} / {size(model.job.bytes_total)}{ui("（目前已知檔案總量）")}</span>}
              {model.job.transfer_bytes_done!==undefined&&<small>{ui("網路已接收：")}{size(model.job.transfer_bytes_done)}{ui("（快取與壓縮會影響實際接收量）")}</small>}
            </>:<progress/>}
            {model.job.files_total>0&&<small>{model.job.stage==="verify"?ui("校驗進度："):ui("已完成檔案：")}{model.job.files_done}/{model.job.files_total}</small>}
          </>}
        </div>}
        <div className="manager-actions"><button disabled={busy||active(model.job)} onClick={()=>void download([model.id])}>{model.ready?ui("檢查／修復"):model.local?ui("準備本機模型"):ui("下載／準備")}</button>
          {active(model.job)&&<button disabled={busy} onClick={()=>void cancel(model.id)}>{ui("取消下載")}</button>}
          <button disabled={busy||active(model.job)||!(model.bytes+model.source_bytes)} onClick={()=>{setDeleting(model);setError("");}}>{ui("刪除模型")}</button>
          {model.job&&<button disabled={busy} onClick={()=>setLogModel(logModel===model.id?"":model.id)}>{logModel===model.id?ui("收起紀錄"):ui("詳細紀錄")}</button>}
        </div>
        {logModel===model.id&&<><small>{ui("視窗顯示最近 200 KB 紀錄；完整紀錄可下載。")}</small><a href={`/api/models/${model.id}/log/download`} download>{ui("下載完整模型紀錄")}</a><pre className="model-log" tabIndex={0}>{log||ui("尚無詳細紀錄")}</pre></>}
      </section>)}</div>
    </Modal>;
}
