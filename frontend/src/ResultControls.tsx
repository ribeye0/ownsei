import { Icon } from "./Icon";
import { t as ui } from "./i18n";
import type { Track, TranscriptVersion, TranslationVersion } from "./types";
import { resultStamp as labelDate } from "./resultInfo";
export function ResultControls({
  track,
  versions,
  translations,
  busy,
  running,
  loading,
  models,
  label,
  time,
  onSwitch,
  onDelete,
  onReload,
  onReport,
  filesCount,
  onFiles,
}: {
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
  onDelete: (
    kind: "versions" | "translations",
    id: string,
    label: string,
  ) => void;
  onReload: () => void;
  onReport: () => void;
  filesCount: number;
  onFiles: () => void;
}) {
  const active = translations.find((v) => v.active);
  return (
    <section className="version-controls" aria-label={ui("轉錄與翻譯結果管理")}>
      <h2 className="panel-title">{ui("結果版本")}</h2>
      <div>
        <label>
          {ui("轉錄結果")}
          <select
            aria-label={ui("轉錄版本")}
            disabled={busy || running || !versions.length}
            value={track?.active_version || ""}
            onChange={(e) => onSwitch("versions", e.target.value)}
          >
            {!versions.length && <option value="">{ui("尚未轉錄")}</option>}
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                {label(v)} ·{" "}
                {v.limit_ms ? ui("前 ") + time(v.limit_ms) + "" : ui("整軌")} ·{" "}
                {v.cues}
                {ui(" 句")}
              </option>
            ))}
          </select>
        </label>
        <button
          className="danger"
          disabled={busy || running || !track?.active_version}
          title={ui("刪除此轉錄")}
          aria-label={ui("刪除此轉錄")}
          onClick={() =>
            onDelete(
              "versions",
              track!.active_version!,
              models[
                versions.find((v) => v.version === track?.active_version)
                  ?.model || ""
              ] || ui("轉錄"),
            )
          }
        >
          <Icon name="trash" />
        </button>
      </div>
      <div>
        <label>
          {ui("這份轉錄的中文結果")}
          <select
            aria-label={ui("翻譯版本")}
            disabled={busy || running || !translations.length}
            value={active?.id || ""}
            onChange={(e) => onSwitch("translations", e.target.value)}
          >
            {!translations.length && <option value="">{ui("尚未翻譯")}</option>}
            {translations.map((v) => (
              <option key={v.id} value={v.id}>
                {models[v.model] || v.model} ·{" "}
                {v.target_language === "zh-Hans" ? ui("简体") : ui("繁體")} ·{" "}
                {v.stage === "review"
                  ? ui("審校稿")
                  : v.scope === "selected"
                    ? ui("局部重譯")
                    : ui("初譯")}{" "}
                · {labelDate(v.created_at)} · {v.translated}
                {ui(" 句")}
                {v.stage === "review"
                  ? ui(" · 審校 ") + (v.reviewed || 0) + ui(" 句")
                  : ""}
                {v.status === "partial"
                  ? ui(" · 部分完成")
                  : v.status === "stale"
                    ? ui(" · 來源待更新")
                    : ""}
                {v.stale ? " · " + v.stale + ui(" 句待更新") : ""}
                {v.manual ? " · " + v.manual + ui(" 句人工校對") : ""}
              </option>
            ))}
          </select>
        </label>
        <button
          className="danger"
          disabled={busy || running || !active}
          title={ui("刪除此譯文")}
          aria-label={ui("刪除此譯文")}
          onClick={() =>
            onDelete(
              "translations",
              active!.id,
              models[active!.model] || active!.model,
            )
          }
        >
          <Icon name="trash" />
        </button>
      </div>
      <footer className="version-footer">
        <small className="sr-only" role="status">
          {busy
            ? ui("正在處理介面操作…")
            : loading
              ? ui("正在讀取結果…")
              : running
                ? ui("任務執行中，完成後可切換結果")
                : ui("每次處理各自保存，可隨時切換版本")}
        </small>
        <details>
          <summary>{ui("結果操作")}</summary>
          <div className="version-menu">
            <button disabled={busy || !track} onClick={onReload}>
              {ui("重新載入結果")}
            </button>
            <button disabled={!track?.active_version} onClick={onReport}>
              {ui("回報此結果")}
            </button>
            <button disabled={!filesCount} onClick={onFiles}>
              {ui("既有匯出檔案（")}
              {filesCount}）
            </button>
          </div>
        </details>
      </footer>
    </section>
  );
}
