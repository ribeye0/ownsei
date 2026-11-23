import type { ReactNode, RefObject } from "react";
import type { Cue } from "./types";
import { t as ui } from "./i18n";
import { Icon } from "./Icon";
import { statusLabels, reviewLabels } from "./labels";

type TranscriptListProps = {
  cues: Cue[];
  total: number;
  hasTrack: boolean;
  active: string;
  now: number;
  selected: string[];
  onSelect: (ids: string[]) => void;
  onPlay: (cue: Cue) => void;
  onEdit: (cue: Cue) => void;
  onReport: (cue: Cue) => void;
  time: (milliseconds: number) => string;
  listRef: RefObject<HTMLElement | null>;
  rowRefs: RefObject<Record<string, HTMLDivElement | null>>;
  renderEditor: (cue: Cue) => ReactNode;
};
export function TranscriptList({
  cues,
  total,
  hasTrack,
  active,
  now,
  selected,
  onSelect,
  onPlay,
  onEdit,
  onReport,
  time,
  listRef,
  rowRefs,
  renderEditor,
}: TranscriptListProps) {
  const status = statusLabels(),
    reviewFlagLabels = reviewLabels();
  return (
    <section className="transcript" ref={listRef}>
      {cues.length ? (
        cues.map((c) => (
          <div
            key={c.id}
            ref={(el) => {
              rowRefs.current[c.id] = el;
            }}
            className={
              "cue " +
              (active === c.id ? "active" : "") +
              " " +
              (now * 1000 >= c.start_ms && now * 1000 < c.end_ms
                ? "playing"
                : "")
            }
          >
            <div className="cue-time">
              <label>
                <input
                  type="checkbox"
                  checked={selected.includes(c.id)}
                  onChange={(e) =>
                    onSelect(
                      e.target.checked
                        ? [...selected, c.id]
                        : selected.filter((id) => id !== c.id),
                    )
                  }
                />
                <span>#{c.ordinal + 1}</span>
              </label>
              <button className="time-link" onClick={() => onPlay(c)}>
                {time(c.start_ms)}
                <small>{time(c.end_ms)} ▷</small>
              </button>
            </div>
            <div className="ja" lang="ja" onClick={() => onEdit(c)}>
              {c.ja}
              {c.ja_override !== null && (
                <small className="edited">{ui("已校對日文")}</small>
              )}
            </div>
            <div className="zh" onClick={() => onEdit(c)}>
              {c.zh || <span className="muted">{ui("等待翻譯")}</span>}
            </div>
            <div className="cue-status">
              <span className={"badge " + c.translation_status}>
                {status[c.translation_status]}
              </span>
              <span className={"badge timing " + c.timing_status}>
                {status[c.timing_status] || c.timing_status}
              </span>
              {c.review_flags.length > 0 && (
                <span
                  className="flag"
                  title={c.review_flags
                    .map((flag) => reviewFlagLabels[flag] || flag)
                    .join("；")}
                >
                  {c.review_flags.includes("alignment_zero_duration") ||
                  c.review_flags.includes("alignment_native_fallback")
                    ? ui("! 時間需回聽")
                    : ui("! 疑點")}
                </span>
              )}
              <button
                title={ui("編輯台詞")}
                aria-label={ui("編輯句段 #") + (c.ordinal + 1)}
                onClick={() => onEdit(c)}
              >
                <Icon name="edit" />
              </button>
              <button
                aria-label={ui("回報句段 #") + (c.ordinal + 1) + ""}
                title={ui("回報此句段")}
                onClick={() => onReport(c)}
              >
                <Icon name="flag" />
              </button>
            </div>
            {renderEditor(c)}
          </div>
        ))
      ) : (
        <div className="empty">
          <h3>
            {total
              ? ui("沒有符合的台詞")
              : hasTrack
                ? ui("還沒有逐字稿")
                : ui("先匯入一條音軌")}
          </h3>
          <p>
            {total
              ? ui("試著調整搜尋或取消疑點篩選")
              : hasTrack
                ? ui("點選「處理音軌」開始轉錄，結果會顯示在這裡。")
                : ui("支援 WAV、MP3、FLAC、M4A、OGG；可匯入單檔或資料夾。")}
          </p>
        </div>
      )}
    </section>
  );
}
