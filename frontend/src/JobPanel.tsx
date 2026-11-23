import { t as ui } from "./i18n";
import { liveJob, stageProgress } from "./jobState";
import { useEffect, useState } from "react";
import { api } from "./api";
export type Job = {
  id: string;
  action: string;
  status: string;
  message: string;
  progress: number;
  created_at?: number;
  started_at?: number;
  finished_at?: number;
  options?: {
    prototype?: unknown;
    asr?: string;
    translation?: string;
    extra_alignment?: boolean;
    track_ids?: string[] | null;
  };
  stages?: Record<
    string,
    Record<
      string,
      {
        status: string;
        progress?: number;
        review_cues?: number;
        resumed_from?: string;
      }
    >
  >;
  tracks?: {
    id: string;
    name: string;
  }[];
  current_track_name?: string;
  track_number?: number;
  tracks_total?: number;
  track_progress?: number;
  track_outcomes?: Record<
    string,
    {
      status: string;
      error?: string;
    }
  >;
  current_track?: string;
  current_stage?: string;
  failed_stage?: string;
  heartbeat_age_seconds?: number;
  metrics?: {
    wall_seconds?: number;
    peak_device_used_mb?: number;
  };
};
const live = liveJob;
function JobLog({ base, job }: { base: string; job: Job }) {
  const [text, setText] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false,
      pending = false;
    const controller = new AbortController();
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const result = await api<{
          text: string;
        }>(`${base}/jobs/${job.id}/log`, "GET", undefined, {
          signal: controller.signal,
        });
        if (!cancelled) {
          setText(result.text);
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = live(job.status) ? window.setInterval(load, 2000) : undefined;
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [base, job.id, job.status]);
  return (
    <div className="job-log">
      <div className="log-tools">
        <span>
          {live(job.status)
            ? ui("每 2 秒更新；可捲動查看全部內容")
            : ui("完整紀錄；可捲動、選取與搜尋文字")}
        </span>
        <a href={`/api${base}/jobs/${job.id}/log/download`} download>
          {ui("下載完整日誌")}
        </a>
      </div>
      {error && (
        <p className="error" role="alert">
          {ui("日誌讀取失敗：")}
          {error}
        </p>
      )}
      <pre
        tabIndex={0}
        aria-label={ui("任務 ") + job.id.slice(0, 8) + ui(" 完整日誌")}
      >
        {text || ui("正在讀取日誌…")}
      </pre>
    </div>
  );
}
export function JobPanel({
  base,
  jobs,
  busy,
  onAction,
}: {
  base: string;
  jobs: Job[];
  busy: boolean;
  onAction: (id: string, action: "cancel" | "resume") => void;
}) {
  const states: Record<string, string> = {
    queued: ui("排隊"),
    waiting: ui("等待 GPU"),
    running: ui("執行中"),
    completed: ui("完成"),
    partial: ui("部分完成"),
    cancelling: ui("正在取消"),
    failed: ui("失敗"),
    cancelled: ui("已取消"),
    interrupted: ui("已中斷"),
  };
  const actions: Record<string, string> = {
    run: ui("轉錄 → 翻譯 → 匯出"),
    transcribe: ui("日文轉錄"),
    translate: ui("中文翻譯"),
    review: ui("全文雙語審校"),
    realign: ui("時間對齊"),
    export: ui("匯出"),
  };
  const [history, setHistory] = useState(false),
    [opened, setOpened] = useState<Record<string, boolean>>({});
  const [expanded, setExpanded] = useState(true);
  const [query, setQuery] = useState("");
  useEffect(() => {
    setHistory(false);
    setOpened({});
    setQuery("");
  }, [base]);
  const visible = (history ? jobs : jobs.slice(0, 3)).filter((j) =>
    `${j.id} ${j.message} ${actions[j.action]} ${states[j.status]}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <section className="jobs" aria-label={ui("任務紀錄")}>
      <div className="panel-heading">
        <button aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
          {expanded ? "−" : "+"}
          {ui(" 任務紀錄")}
        </button>
        <span>
          {jobs.filter((j) => live(j.status)).length}
          {ui(" 個活動任務")}
        </span>
        {jobs.some((j) =>
          !j.options?.prototype && ["failed", "partial", "interrupted"].includes(j.status),
        ) && <span className="error-indicator">{ui("有未完成任務")}</span>}
        {!expanded && jobs.find((j) => live(j.status)) && (
          <span>
            {jobs.find((j) => live(j.status))?.current_track_name} ·{" "}
            {Math.round(
              (jobs.find((j) => live(j.status))?.progress || 0) * 100,
            )}
            %
          </span>
        )}
        {expanded && jobs.length > 3 && (
          <button onClick={() => setHistory(!history)}>
            {history
              ? ui("僅看最近 3 筆")
              : ui("顯示最近 ") + jobs.length + ui(" 筆")}
          </button>
        )}
      </div>
      {expanded && (
        <>
          {history && (
            <input
              className="job-search"
              aria-label={ui("搜尋任務")}
              placeholder={ui("搜尋任務 ID、狀態或訊息")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          )}
          {!jobs.length && (
            <p className="panel-empty">
              {ui("尚無任務。選擇音軌與模型後開始轉錄或翻譯。")}
            </p>
          )}
          {jobs.length > 0 && !visible.length && (
            <p className="panel-empty">{ui("沒有符合的任務。")}</p>
          )}
          {visible.map((j) => (
            <article className={`job ${j.status}`} key={j.id}>
              <div className="job-summary">
                <strong className="job-state">
                  [{states[j.status] || j.status}]
                </strong>
                <span className="job-action">
                  {actions[j.action] || j.action}
                </span>
                <code title={j.id}>{j.id.slice(0, 8)}</code>
                <span className="job-date">
                  {j.created_at
                    ? new Date(j.created_at * 1000).toLocaleString("zh-TW", {
                        hour12: false,
                      })
                    : ""}
                </span>
                {j.metrics?.wall_seconds !== undefined && (
                  <span>
                    {j.metrics.wall_seconds.toFixed(1)} s
                    {j.metrics.peak_device_used_mb !== undefined &&
                      ui(" / 顯存峰值 ") +
                        j.metrics.peak_device_used_mb +
                        " MiB"}
                  </span>
                )}
                <div className="job-actions">
                  {live(j.status) ? (
                    <button
                      disabled={busy || j.status === "cancelling"}
                      onClick={() => onAction(j.id, "cancel")}
                    >
                      {ui("取消")}
                    </button>
                  ) : (
                    !j.options?.prototype && ["partial", "failed", "cancelled", "interrupted"].includes(
                      j.status,
                    ) && (
                      <button
                        disabled={busy}
                        onClick={() => onAction(j.id, "resume")}
                      >
                        {ui("恢復")}
                      </button>
                    )
                  )}
                  <button
                    aria-expanded={!!opened[j.id]}
                    onClick={() =>
                      setOpened({ ...opened, [j.id]: !opened[j.id] })
                    }
                  >
                    {opened[j.id] ? ui("− 收起日誌") : ui("+ 展開日誌")}
                  </button>
                </div>
              </div>
              {j.tracks_total && (
                <p className="batch-current">
                  {ui("第 ")}
                  {j.track_number || 0}/{j.tracks_total}
                  {ui(" 條音軌 · ")}
                  {j.current_track_name}
                </p>
              )}
              <div className="job-message">{j.message}</div>
              {j.stages && (
                <div className="job-stages" aria-label={ui("音軌任務階段")}>
                  {(
                    j.tracks ||
                    Object.keys(j.stages).map((id) => ({ id, name: id }))
                  ).map((track) => (
                    <div className="job-track" key={track.id}>
                      <strong>{track.name}</strong>
                      <span>
                        {Object.entries(j.stages?.[track.id] || {}).map(
                          ([name, stage]) => (
                            <span key={name}>
                              {{
                                transcribe: ui("轉錄"),
                                align: ui("對齊"),
                                translate: ui("翻譯"),
                                export: ui("匯出"),
                              }[name] || name}
                              ：{states[stage.status] || stage.status}
                              {stage.resumed_from ? ui("（沿用）") : ""}
                              {name === "align" && stage.review_cues
                                ? " · " + stage.review_cues + ui(" 句需回聽")
                                : ""}
                              {"　"}
                            </span>
                          ),
                        )}
                        {!j.stages?.[track.id] && <span>{ui("等待處理")}</span>}
                      </span>
                      {j.track_outcomes?.[track.id]?.error && (
                        <p className="error">
                          {j.track_outcomes[track.id].error}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {live(j.status) &&
                j.heartbeat_age_seconds !== undefined &&
                j.heartbeat_age_seconds > 15 && (
                  <p role="alert">
                    {ui("任務心跳已 ")}
                    {Math.floor(j.heartbeat_age_seconds)}
                    {ui(" 秒未更新，請展開日誌確認程序狀態。")}
                  </p>
                )}
              <div className="job-progress">
                {stageProgress(j).map((stage) => (
                  <div
                    className={"stage-progress " + stage.status}
                    key={stage.name}
                  >
                    <div className="stage-caption">
                      <strong>
                        {
                          {
                            transcribe: ui("轉錄"),
                            translate: ui("翻譯"),
                            export: ui("匯出"),
                          }[stage.name]
                        }
                      </strong>
                      <span>{Math.round(stage.value * 100)}%</span>
                    </div>
                    <progress
                      max={1}
                      value={stage.value}
                      aria-label={
                        ui("任務 ") +
                        j.id.slice(0, 8) +
                        " " +
                        ({
                          transcribe: ui("轉錄進度"),
                          translate: ui("翻譯進度"),
                          export: ui("匯出進度"),
                        }[stage.name] || stage.name)
                      }
                    />
                    <small>
                      {stage.done}/{stage.total}
                      {ui(" 條音軌完成")}
                      {" · "}
                      {states[stage.status] || ui("等待處理")}
                    </small>
                  </div>
                ))}
              </div>
              {opened[j.id] && <JobLog base={base} job={j} />}
            </article>
          ))}
        </>
      )}
    </section>
  );
}
