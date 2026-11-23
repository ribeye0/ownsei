import {t as ui} from "./i18n";
export type TranslationOptions = {context: number; names: boolean; history: boolean; compact_rows?: boolean};
export const contextSizes = [8192,16384,32768,65536,98304,131072,262144];
export function translationDefaults(model: string): TranslationOptions {
  return model === "index-nativelong-9b" ? {context:65536,names:false,history:true} : {context:8192,names:true,history:false};
}
export function TranslationSettings({value,onChange,model,disabled=false}: {
  model:string;value:TranslationOptions;onChange:(value:TranslationOptions)=>void;disabled?:boolean;
}) {
  return <details className="translation-experiments native-long-settings"><summary>{ui("翻譯處理設定")}</summary>
    <label>{ui("上下文容量")}<select aria-label={ui("翻譯上下文容量")} value={value.context} disabled={disabled}
      onChange={e=>onChange({...value,context:Number(e.target.value)})}>
      {contextSizes.map(size=><option key={size} value={size}>{size/1024}K</option>)}
    </select></label>
    <label className="check"><input type="checkbox" checked={value.history} disabled={disabled}
      onChange={e=>onChange({...value,history:e.target.checked})}/>{ui("參照前批已完成的中文譯文")}</label>
    <label className="check"><input type="checkbox" checked={value.names} disabled={disabled}
      onChange={e=>onChange({...value,names:e.target.checked})}/>{ui("套用共享人物譯名記憶")}</label>
    {model==="index-nativelong-9b" && <label className="check"><input type="checkbox" checked={value.compact_rows ?? false} disabled={disabled}
      onChange={e=>onChange({...value,compact_rows:e.target.checked})}/>{ui("精簡輸出格式（實驗性）")}</label>}
    <small>{ui("輸入與輸出共用上下文容量；容量不足時縮減參照或拆批。人工術語優先於自動人物譯名。")}</small>
  </details>;
}
