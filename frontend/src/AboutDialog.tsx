import {useEffect, useState} from "react";
import {api} from "./api";
import {Modal} from "./Modal";
import {t as ui} from "./i18n";

type Update = {
  phase: "idle" | "checking" | "current" | "available" | "downloading" | "ready" | "installing" | "error";
  current: string; managed: boolean; version: string; bytes: number; total: number; error: string;
};

export function AboutDialog({onClose, version, draftDirty}: {onClose: () => void; version: string; draftDirty: boolean}) {
  const [status, setStatus] = useState<Update | null>(null);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const phase = status?.phase;
  useEffect(() => {
    if (phase && !["checking", "downloading", "installing"].includes(phase)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function refresh() {
      try {
        const result = await api<Update>("/update", "GET", undefined, {timeoutMs: 4000});
        if (!active) return;
        if (result.current !== version && phase === "installing") {
          location.reload();
          return;
        }
        setStatus(result);
        setError("");
        failures = 0;
      } catch (e) {
        if (!active) return;
        if (phase !== "installing") setError(String(e));
        else if (++failures >= 30) setError(ui("安裝仍未回應，請查看 Ownsei 後端視窗。"));
      }
      if (active) timer = setTimeout(refresh, 1000);
    }
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, [phase, version]);

  async function action(name: "check" | "download" | "install") {
    setSending(true);
    setError("");
    try {
      setStatus(await api<Update>("/update/" + name, "POST", {}));
    } catch (e) {
      setError(ui(e instanceof Error ? e.message : String(e)));
    } finally {
      setSending(false);
    }
  }
  const active = phase === "checking" || phase === "downloading" || phase === "installing";
  return <Modal title={ui("關於 Ownsei")} onClose={onClose} busy={phase === "installing"} className="about-dialog">
    <div><b>Ownsei v{version}</b>
      <p>{ui("在本機將日語音聲轉錄為日文逐字稿，翻譯成中文字幕，並依原音校對與匯出。")}</p>
    </div>
    <div role="status">
      {phase === "checking" && ui("正在檢查更新…")}
      {phase === "current" && ui("已是最新版本")}
      {status && phase === "available" && <>{ui("有新版本")} v{status.version}
        {!status.managed && <p>{ui("此啟動方式請透過 GitHub Releases 手動更新。")}</p>}</>}
      {status && phase === "downloading" && <>{ui("正在背景下載")} v{status.version}
        <p><progress aria-label={ui("下載更新")} value={status.bytes} max={status.total || 1}/> {Math.floor(100 * status.bytes / (status.total || 1))}%</p>
        {ui("可以關閉此視窗繼續使用 Ownsei。")}</>}
      {status && phase === "ready" && <>{ui("已下載並驗證")} v{status.version}
        {draftDirty && <p>{ui("安裝前請先儲存或放棄字幕草稿。")}</p>}</>}
      {phase === "installing" && ui("正在安裝並重新啟動，完成後將重新載入頁面。")}
    </div>
    {(error || status?.error) && <p role="alert">{error || ui(status!.error)}</p>}
    <div className="manager-actions">
      <button type="button" disabled={sending || active || !status} onClick={() => void action("check")}>{ui("檢查更新")}</button>
      {status && phase === "available" && status.managed &&
        <button type="button" disabled={sending} onClick={() => void action("download")}>{ui("下載更新")}</button>}
      {status && phase === "ready" &&
        <button className="primary" type="button" disabled={sending || draftDirty} onClick={() => void action("install")}>{ui("安裝並重新啟動")}</button>}
      <a href="https://github.com/ribeye0/ownsei/releases/latest" target="_blank" rel="noopener noreferrer">GitHub Releases ↗</a>
    </div>
  </Modal>;
}
