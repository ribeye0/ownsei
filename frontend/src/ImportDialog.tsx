import { t as ui } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Modal } from "./Modal";
import { Icon } from "./Icon";
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
  variants_skipped?: number;
  current_file: string;
  error: string | null;
};
const bytes = (n: number) =>
  n >= 1024 ** 3
    ? `${(n / 1024 ** 3).toFixed(2)} GiB`
    : `${(n / 1024 ** 2).toFixed(1)} MiB`;
export function ImportDialog({
  base,
  onClose,
  onChanged,
}: {
  base: string;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const stages: Record<string, string> = {
    waiting: ui("等待匯入"),
    scanning: ui("掃描音檔"),
    hashing: ui("讀取與計算檔案雜湊"),
    cached: ui("使用已核對的雜湊"),
    duplicate: ui("略過重複音檔"),
    probing: ui("讀取音訊資訊"),
    saving: ui("保存音軌資訊"),
    checked: ui("檔案檢查完成"),
    completed: ui("匯入完成"),
  };
  const [picking, setPicking] = useState(false);
  const [path, setPath] = useState(""),
    [job, setJob] = useState<ImportJob | null>(null);
  const [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [submitting, setSubmitting] = useState(false);
  const [tick, setTick] = useState(Date.now());
  const alive = useRef(true),
    changed = useRef(onChanged);
  changed.current = onChanged;
  const running = job?.status === "running";
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void api<ImportJob | null>(base + "/imports", "GET", undefined, {
      signal: controller.signal,
    })
      .then((value) => {
        if (alive.current) {
          setJob(value);
          if (value?.status === "running") setPath(value.source);
        }
      })
      .catch((e) => {
        if (alive.current) setError(String(e));
      })
      .finally(() => {
        if (alive.current) setLoading(false);
      });
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [base]);
  useEffect(() => {
    if (!running || !job) return;
    const controller = new AbortController();
    let cancelled = false,
      timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api<ImportJob>(
          base + "/imports/" + job.id,
          "GET",
          undefined,
          { signal: controller.signal },
        );
        if (cancelled) return;
        setJob(next);
        setTick(Date.now());
        setError("");
        if (next.status !== "running") {
          await changed.current();
          return;
        }
      } catch (e) {
        if (!cancelled)
          setError(ui("進度讀取失敗，會自動重試：") + String(e) + "");
      }
      if (!cancelled) timer = setTimeout(poll, 500);
    };
    void poll();
    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [base, job?.id, running]);
  async function browse(kind: "file" | "directory") {
    setPicking(true);
    setError("");
    try {
      const value = await api<{ path: string | null }>(
        "/native-picker",
        "POST",
        { kind, initial: path },
        { timeoutMs: 310000 },
      );
      if (alive.current && value.path) setPath(value.path);
    } catch (e) {
      if (alive.current) setError(String(e));
    } finally {
      if (alive.current) setPicking(false);
    }
  }
  const submit = async () => {
    setSubmitting(true);
    setError("");
    try {
      const next = await api<ImportJob>(base + "/imports", "POST", { path });
      if (alive.current) {
        setJob(next);
        setTick(Date.now());
      }
    } catch (e) {
      if (alive.current) setError(String(e));
    } finally {
      if (alive.current) setSubmitting(false);
    }
  };
  const percent = !job
    ? 0
    : job.status === "completed"
      ? 100
      : Math.min(
          99,
          job.bytes_total
            ? Math.floor((job.bytes_done / job.bytes_total) * 100)
            : 0,
        );
  return (
    <Modal
      title={ui("匯入本機音檔")}
      onClose={onClose}
      busy={picking}
      className="import-dialog"
    >
      <p>{ui("選擇音檔或資料夾，或填入完整路徑。原音留在原位置。")}</p>
      <div className="import-choices">
        <button
          disabled={loading || running || submitting || picking}
          onClick={() => void browse("file")}
        >
          <Icon name="audio" />
          {ui("選擇音檔")}
        </button>
        <button
          disabled={loading || running || submitting || picking}
          onClick={() => void browse("directory")}
        >
          <Icon name="folder" />
          {ui("選擇資料夾")}
        </button>
      </div>
      {picking && (
        <p role="status">{ui("請在系統檔案選擇器中完成選擇，或按取消返回")}</p>
      )}
      <label>
        {ui("匯入來源")}
        <input
          value={path}
          disabled={loading || running || submitting || picking}
          onChange={(e) => setPath(e.target.value)}
          placeholder={ui("選擇音檔、資料夾，或貼上完整路徑")}
        />
      </label>
      <small>
        {ui(
          "遞迴匯入子目錄；同名且長度相符的 WAV／MP3 只匯入 WAV，支援 WAV、MP3 分資料夾存放。原音留在原處。",
        )}
      </small>
      <div className="dialog-footer">
        <button disabled={picking} onClick={onClose}>
          {ui("關閉")}
        </button>
        <button
          className="primary"
          disabled={loading || running || submitting || picking || !path.trim()}
          onClick={() => void submit()}
        >
          {loading
            ? ui("讀取匯入狀態…")
            : running || submitting
              ? ui("匯入中…")
              : ui("開始匯入")}
        </button>
      </div>
      {job && (
        <div className="import-progress" aria-label={ui("匯入進度")}>
          <div className="import-heading" role="status">
            {job.status === "failed"
              ? ui("匯入失敗")
              : job.status === "interrupted"
                ? ui("匯入已中斷")
                : stages[job.stage] || job.stage}
            <strong>{percent}%</strong>
          </div>
          <progress aria-label={ui("音檔檢查進度")} max={100} value={percent} />
          <p>
            {job.files_done} / {job.files_total}
            {ui(" 個檔案 · ")}
            {bytes(job.bytes_done)} / {bytes(job.bytes_total)}
            {ui(" 已檢查")}
          </p>
          <p>
            {ui("略過同軌 MP3：")}
            {job.variants_skipped || 0}
          </p>
          <p>
            {ui("新增 ")}
            {job.added}
            {ui(" · 略過 ")}
            {job.skipped}
            {ui(" · 使用快取 ")}
            {job.cached}
          </p>
          {job.current_file && (
            <div className="import-file" title={job.current_file}>
              {job.current_file}
            </div>
          )}
          <small>
            {ui("實際讀取 ")}
            {bytes(job.bytes_read)}
            {running
              ? ui(" · 已執行 ") +
                Math.max(0, Math.floor(tick / 1000 - job.started_at)) +
                ui(" 秒")
              : ""}
          </small>
          {running && (
            <p className="muted">
              {ui("可關閉視窗，匯入會繼續；再次點「匯入音檔」可查看進度。")}
            </p>
          )}
          {job.error && (
            <p role="alert" className="error">
              {job.error}
              {ui(" 已完成的 ")}
              {job.added}
              {ui(" 條音軌會保留。")}
            </p>
          )}
          <details>
            <summary>{ui("匯入方式與耗時")}</summary>
            <p>
              {ui(
                "第一次會讀完整音檔計算 SHA-256，避免重複匯入，再用 ffprobe 讀取長度與聲道資訊。大型 WAV 需要讀取較多資料；不會在匯入時轉錄、翻譯或轉碼。再次匯入時，檔案位置與檔案狀態相同會沿用已核對的雜湊。",
              )}
            </p>
          </details>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Modal>
  );
}
