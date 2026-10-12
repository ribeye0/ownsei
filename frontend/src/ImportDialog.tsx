import {t as ui} from "./i18n";
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { FilePicker } from "./FilePicker";
type ImportJob = {
    id: string;
    status: string;
    stage: string;
    source: string;
    started_at: number;
    files_total: number;
    files_done: number;
    bytes_total: number;
    bytes_done: number;
    bytes_read: number;
    added: number;
    skipped: number;
    cached: number;
    current_file: string;
    error: string | null;
};
const bytes = (n: number) => n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GiB` : `${(n / 1024 ** 2).toFixed(1)} MiB`;
export function ImportDialog({ base, onClose, onChanged }: {
    base: string;
    onClose: () => void;
    onChanged: () => Promise<void>;
}) {
    const stages: Record<string, string> = { waiting: ui("等待匯入"), scanning: ui("掃描音檔"), hashing: ui("讀取與計算檔案雜湊"),
        cached: ui("使用已核對的雜湊"), duplicate: ui("略過重複音檔"), probing: ui("讀取音訊資訊"), saving: ui("保存音軌資訊"),
        checked: ui("檔案檢查完成"), completed: ui("匯入完成") };
    const [picker, setPicker] = useState(false);
    const [path, setPath] = useState(""), [job, setJob] = useState<ImportJob | null>(null);
    const [error, setError] = useState(""), [loading, setLoading] = useState(true), [submitting, setSubmitting] = useState(false);
    const [tick, setTick] = useState(Date.now());
    const modal = useRef<HTMLElement>(null), alive = useRef(true), changed = useRef(onChanged), close = useRef(onClose);
    changed.current = onChanged;
    close.current = onClose;
    const running = job?.status === "running";
    useEffect(() => {
        alive.current = true;
        const controller = new AbortController();
        void api<ImportJob | null>(base + "/imports", "GET", undefined, { signal: controller.signal }).then(value => {
            if (alive.current) {
                setJob(value);
                if (value?.status === "running")
                    setPath(value.source);
            }
        }).catch(e => { if (alive.current)
            setError(String(e)); }).finally(() => { if (alive.current)
            setLoading(false); });
        return () => { alive.current = false; controller.abort(); };
    }, [base]);
    useEffect(() => {
        if (!running || !job)
            return;
        const controller = new AbortController();
        let cancelled = false, timer: ReturnType<typeof setTimeout>;
        const poll = async () => {
            try {
                const next = await api<ImportJob>(base + "/imports/" + job.id, "GET", undefined, { signal: controller.signal });
                if (cancelled)
                    return;
                setJob(next);
                setTick(Date.now());
                setError("");
                if (next.status !== "running") {
                    await changed.current();
                    return;
                }
            }
            catch (e) {
                if (!cancelled)
                    setError(ui("進度讀取失敗，會自動重試：") + (String(e)) + "");
            }
            if (!cancelled)
                timer = setTimeout(poll, 500);
        };
        void poll();
        return () => { cancelled = true; controller.abort(); clearTimeout(timer); };
    }, [base, job?.id, running]);
    useEffect(() => {
        const previous = document.activeElement as HTMLElement | null;
        modal.current?.querySelector<HTMLButtonElement>("button")?.focus();
        const key = (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                e.preventDefault();
                close.current();
            }
            if (e.key !== "Tab")
                return;
            const items = Array.from(modal.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), summary") || []);
            const first = items[0], last = items.at(-1);
            if (!modal.current?.contains(document.activeElement)) {
                e.preventDefault();
                first?.focus();
            }
            else if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last?.focus();
            }
            else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first?.focus();
            }
        };
        window.addEventListener("keydown", key);
        return () => { window.removeEventListener("keydown", key); previous?.focus(); };
    }, []);
    useEffect(() => {
        if (!loading && !running)
            modal.current?.querySelector<HTMLInputElement>("input")?.focus();
    }, [loading]);
    const submit = async () => {
        setSubmitting(true);
        setError("");
        try {
            const next = await api<ImportJob>(base + "/imports", "POST", { path });
            if (alive.current) {
                setJob(next);
                setTick(Date.now());
            }
        }
        catch (e) {
            if (alive.current)
                setError(String(e));
        }
        finally {
            if (alive.current)
                setSubmitting(false);
        }
    };
    const percent = !job ? 0 : job.status === "completed" ? 100 : Math.min(99, job.bytes_total ? Math.floor(job.bytes_done / job.bytes_total * 100) : 0);
    return <div className="modal-backdrop"><section ref={modal} className="modal" role="dialog" aria-modal="true" aria-label={ui("匯入本機音檔")}>
    <button className="modal-close icon" aria-label={ui("關閉匯入視窗")} onClick={onClose}>×</button>
    <h2>{ui("匯入本機音檔")}</h2>
    <p>{ui("選擇音檔或資料夾，或填入完整路徑。原音留在原位置。")}</p>
    <label>{ui("本機路徑")}<input value={path} disabled={loading || running || submitting} onChange={e => setPath(e.target.value)} placeholder={ui("/完整音檔或資料夾路徑")}/></label>
    <button disabled={loading || running || submitting} onClick={() => setPicker(true)}>{ui("瀏覽音檔／資料夾")}</button>
    <small>{ui("同資料夾同名 WAV 優先；WAV、MP3 分放不同資料夾時，請選需要的一個版本。")}</small>
    <button className="primary" disabled={loading || running || submitting || !path.trim()} onClick={() => void submit()}>
      {loading ? ui("讀取匯入狀態…") : running || submitting ? ui("匯入中…") : ui("匯入")}
    </button>
    {job && <div className="import-progress" aria-label={ui("匯入進度")}>
      <div className="import-heading" role="status">{job.status === "failed" ? ui("匯入失敗") : job.status === "interrupted" ? ui("匯入已中斷") : stages[job.stage] || job.stage}<strong>{percent}%</strong></div>
      <progress aria-label={ui("音檔檢查進度")} max={100} value={percent}/>
      <p>{job.files_done} / {job.files_total}{ui(" 個檔案 · ")}{bytes(job.bytes_done)} / {bytes(job.bytes_total)}{ui(" 已檢查")}</p>
      <p>{ui("新增 ")}{job.added}{ui(" · 略過 ")}{job.skipped}{ui(" · 使用快取 ")}{job.cached}</p>
      {job.current_file && <div className="import-file" title={job.current_file}>{job.current_file}</div>}
      <small>{ui("實際讀取 ")}{bytes(job.bytes_read)}{running ? ui(" · 已執行 ") + (Math.max(0, Math.floor(tick / 1000 - job.started_at))) + ui(" 秒") : ""}</small>
      {running && <p className="muted">{ui("可關閉視窗，匯入會繼續；再次點「匯入音檔」可查看進度。")}</p>}
      {job.error && <p role="alert" className="error">{job.error}{ui(" 已完成的 ")}{job.added}{ui(" 條音軌會保留。")}</p>}
      <details><summary>{ui("匯入方式與耗時")}</summary><p>{ui("第一次會讀完整音檔計算 SHA-256，避免重複匯入，再用 ffprobe 讀取長度與聲道資訊。大型 WAV 需要讀取較多資料；不會在匯入時轉錄、翻譯或轉碼。再次匯入時，檔案位置與檔案狀態相同會沿用已核對的雜湊。")}</p></details>
    </div>}
    {error && <p className="error" role="alert">{error}</p>}
  </section>{picker && <FilePicker initial={path} onClose={() => setPicker(false)} onSelect={value => { setPath(value); setPicker(false); }}/>}</div>;
}
