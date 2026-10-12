import {t as ui} from "./i18n";
import type { Track, TranscriptVersion, TranslationVersion } from "./types";
import { resultStamp as labelDate } from "./resultInfo";
export function ResultControls({ track, versions, translations, busy, running, loading, models, label, time, onSwitch, onDelete, onReload, onReport }: {
    track?: Track;
    versions: TranscriptVersion[];
    translations: TranslationVersion[];
    busy: boolean;
    running: boolean;
    loading: boolean;
    models: Record<string, string>;
    label: (v: TranscriptVersion) => string;
    time: (ms: number) => string;
    onSwitch: (kind: "versions" | "translations", id: string) => void;
    onDelete: (kind: "versions" | "translations", id: string, label: string) => void;
    onReload: () => void;
    onReport: () => void;
}) {
    const active = translations.find(v => v.active);
    return <section className="version-controls" aria-label={ui("轉錄與翻譯結果管理")}>
    <h2 className="panel-title">{ui("結果版本")}</h2>
    <div><label>{ui("轉錄結果")}<select aria-label={ui("轉錄版本")} disabled={busy || running || !versions.length} value={track?.active_version || ""} onChange={e => onSwitch("versions", e.target.value)}>
        {!versions.length && <option value="">{ui("尚未轉錄")}</option>}
        {versions.map(v => <option key={v.version} value={v.version}>{label(v)} · {v.limit_ms ? ui("前 ") + (time(v.limit_ms)) + "" : ui("整軌")} · {v.cues}{ui(" 句")}</option>)}
      </select>
    </label><button className="danger" disabled={busy || running || !track?.active_version} onClick={() => onDelete("versions", track!.active_version!, models[versions.find(v => v.version === track?.active_version)?.model || ""] || ui("轉錄"))}>{ui("刪除此轉錄")}</button></div>
    <div><label>{ui("這份轉錄的中文結果")}<select aria-label={ui("翻譯版本")} disabled={busy || running || !translations.length} value={active?.id || ""} onChange={e => onSwitch("translations", e.target.value)}>
        {!translations.length && <option value="">{ui("尚未翻譯")}</option>}
        {translations.map(v => <option key={v.id} value={v.id}>{models[v.model] || v.model} · {v.target_language === "zh-Hans" ? ui("简体") : ui("繁體")} · {v.stage === "review" ? ui("審校稿") : v.scope === "selected" ? ui("局部重譯") : ui("初譯")} · {labelDate(v.created_at)} · {v.translated}{ui(" 句")}{v.stage === "review" ? ui(" · 審校 ") + (v.reviewed || 0) + ui(" 句") : ""}{v.status === "partial" ? ui(" · 部分完成") : v.status === "stale" ? ui(" · 來源待更新") : ""}{v.stale ? " · " + (v.stale) + ui(" 句待更新") : ""}{v.manual ? " · " + (v.manual) + ui(" 句人工校對") : ""}</option>)}
      </select>
    </label><button className="danger" disabled={busy || running || !active} onClick={() => onDelete("translations", active!.id, models[active!.model] || active!.model)}>{ui("刪除此譯文")}</button></div>
    <small>{ui("每次翻譯另存初譯；審校稿保留來源關係。刪除初譯不會刪除其審校稿。")}</small>
    <small role="status">{busy ? ui("正在處理介面操作…") : loading ? ui("正在讀取結果…") : running ? ui("作品任務執行中，完成後可切換或刪除結果") : ui("結果已載入")}</small>
    <button disabled={busy || !track} onClick={onReload}>{ui("重新載入結果")}</button>
    <button disabled={!track?.active_version} onClick={onReport}>{ui("回報此結果")}</button>
  </section>;
}
