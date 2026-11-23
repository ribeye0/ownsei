import { readPreference, writePreference } from "./interfacePreferences";
import { t as ui } from "./i18n";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";
import {
  currentSubtitles,
  playbackRates,
  playbackTime,
  seekPosition,
  type PlaybackCue,
} from "./playback";
import { MediaVolume } from "./audioGain";
import { AudioTransport, type TransportState } from "./audioTransport";
type Props = {
  tracks: {
    id: string;
    name: string;
  }[];
  onTrack: (id: string) => void;
  switchingDisabled: boolean;
  track: {
    id: string;
    name: string;
    duration_ms: number;
  };
  source: string;
  projectTitle: string;
  versionLabel: string;
  cues: PlaybackCue[];
  audioRef: RefObject<HTMLAudioElement | null>;
  now: number;
  onTime: (seconds: number) => void;
  followPlayback: boolean;
  onFollowPlayback: (value: boolean) => void;
  segmentEnd: number | null;
  onSegmentEnd: (seconds: number | null) => void;
  expanded: boolean;
  onExpanded: (value: boolean) => void;
};
function PlayIcon({ playing }: { playing: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {playing ? (
        <>
          <rect x="6" y="4" width="4" height="16" />
          <rect x="14" y="4" width="4" height="16" />
        </>
      ) : (
        <path d="M7 3v18l15-9z" />
      )}
    </svg>
  );
}
export function AudioPlayer({
  track,
  source,
  projectTitle,
  versionLabel,
  cues,
  audioRef,
  now,
  onTime,
  segmentEnd,
  onSegmentEnd,
  expanded,
  onExpanded,
  tracks,
  onTrack,
  switchingDisabled,
  followPlayback: follow,
  onFollowPlayback,
}: Props) {
  const [playlist, setPlaylist] = useState(false),
    [trackQuery, setTrackQuery] = useState("");
  const trackIndex = tracks.findIndex((t) => t.id === track.id);
  const listPanel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!playlist) return;
    const click = (e: MouseEvent) => {
      if (
        !listPanel.current?.contains(e.target as Node) &&
        !(
          e.target instanceof Element &&
          e.target.closest("[data-playlist-toggle]")
        )
      )
        setPlaylist(false);
    };
    document.addEventListener("mousedown", click);
    return () => document.removeEventListener("mousedown", click);
  }, [playlist]);
  const [state, setState] = useState<TransportState>("paused");
  const playing = state !== "paused";
  const [duration, setDuration] = useState(track.duration_ms / 1000);
  const [rate, setRate] = useState(() => {
    const stored = Number(readPreference("onsei:playback-rate") || 1);
    return playbackRates.includes(stored) ? stored : 1;
  });
  const [volume, setVolume] = useState(1),
    [muted, setMuted] = useState(false);
  const [error, setError] = useState("");
  const [diagnostics, setDiagnostics] = useState<string[]>([]);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const dock = useRef<HTMLElement>(null),
    sheet = useRef<HTMLElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null),
    transcript = useRef<HTMLDivElement>(null);
  const cueRows = useRef<Record<string, HTMLButtonElement | null>>({});
  const gainRef = useRef<MediaVolume | null>(null);
  const transportRef = useRef<AudioTransport | null>(null);
  const segmentRef = useRef(segmentEnd);
  segmentRef.current = segmentEnd;
  const [scrub, setScrub] = useState<number | null>(null);
  const dragging = useRef(false);
  const closeRef = useRef(onExpanded),
    toggleRef = useRef<() => void>(() => {});
  closeRef.current = onExpanded;
  const position = seekPosition(scrub ?? now, duration);
  const subtitles = useMemo(() => currentSubtitles(cues, now), [cues, now]);
  const activeId = subtitles.ids[0];
  useEffect(() => {
    const media = audioRef.current;
    if (!media) return;
    const gain = new MediaVolume(media);
    const transport = new AudioTransport(
      media,
      () => gain.resume(),
      setState,
      setError,
      undefined,
      (message) => {
        const ranges = (value: TimeRanges) =>
          Array.from(
            { length: value.length },
            (_, i) => `${value.start(i).toFixed(2)}–${value.end(i).toFixed(2)}`,
          ).join(",") || "none";
        const line = `${new Date().toISOString()} ${message} time=${media.currentTime.toFixed(3)} paused=${media.paused} seeking=${media.seeking} ready=${media.readyState} network=${media.networkState} preload=${media.preload} source=${media.currentSrc || media.getAttribute("src")} route=${gain.state} volume=${media.volume} muted=${media.muted} rate=${media.playbackRate} buffered=[${ranges(media.buffered)}] seekable=[${ranges(media.seekable)}]`;
        setDiagnostics((previous) => [...previous.slice(-119), line]);
      },
    );
    gainRef.current = gain;
    transportRef.current = transport;
    return () => {
      gain.dispose();
      transport.dispose();
      gainRef.current = null;
      transportRef.current = null;
    };
  }, [audioRef]);
  useEffect(() => {
    dragging.current = false;
    setScrub(null);
  }, [expanded]);
  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = rate;
    writePreference("onsei:playback-rate", String(rate));
  }, [rate, audioRef]);
  useEffect(() => {
    if (!dock.current) return;
    const observer = new ResizeObserver(() => {
      if (dock.current)
        document.documentElement.style.setProperty(
          "--player-height",
          `${dock.current.getBoundingClientRect().height}px`,
        );
    });
    observer.observe(dock.current);
    return () => observer.disconnect();
  }, [expanded]);
  useEffect(() => {
    if (!expanded) return;
    const previousFocus = document.activeElement;
    closeButton.current?.focus();
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current(false);
      }
      const target = event.target instanceof Element ? event.target : null;
      if (
        event.code === "Space" &&
        !target?.closest("button,input,select,textarea,summary,a")
      ) {
        event.preventDefault();
        toggleRef.current();
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        sheet.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled),input:not(:disabled),select:not(:disabled),summary",
        ) || [],
      ).filter((e) => {
        // Closed details can retain layout rectangles after being expanded in Chromium.
        const closed = e.closest("details:not([open])");
        return (
          e.getClientRects().length > 0 &&
          (!closed || closed.querySelector(":scope > summary") === e)
        );
      });
      const first = focusable[0],
        last = focusable[focusable.length - 1];
      if (!first) {
        event.preventDefault();
        return;
      }
      if (!sheet.current?.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", listener);
    return () => {
      window.removeEventListener("keydown", listener);
      if (
        previousFocus instanceof HTMLElement &&
        previousFocus !== document.body &&
        previousFocus.isConnected &&
        !previousFocus.closest("[inert]")
      )
        previousFocus.focus();
      else
        dock.current?.querySelector<HTMLButtonElement>(".dock-title")?.focus();
    };
  }, [expanded]);
  useEffect(() => {
    const row = activeId && cueRows.current[activeId],
      list = transcript.current;
    if (transcriptOpen && follow && row && list) {
      list.scrollTo({
        top: Math.max(
          0,
          row.offsetTop - (list.clientHeight - row.clientHeight) / 2,
        ),
      });
    }
  }, [activeId, transcriptOpen, follow]);
  function sync() {
    const media = audioRef.current;
    if (!media) return;
    onTime(media.currentTime);
    if (
      segmentRef.current !== null &&
      media.currentTime >= segmentRef.current
    ) {
      media.pause();
      segmentRef.current = null;
      onSegmentEnd(null);
    }
  }
  function seek(seconds: number) {
    const media = audioRef.current;
    if (!media) return;
    segmentRef.current = null;
    onSegmentEnd(null);
    const next = seekPosition(seconds, duration);
    transportRef.current?.seek(next);
    onTime(next);
  }
  function toggle() {
    const media = audioRef.current;
    if (!media) return;
    setError("");
    segmentRef.current = null;
    onSegmentEnd(null);
    transportRef.current?.toggle();
  }
  toggleRef.current = toggle;
  function resumeGain() {
    void gainRef.current
      ?.resume()
      .catch((e: unknown) =>
        setError(ui("音量增益啟用失敗：") + String(e) + ""),
      );
  }
  function changeVolume(value: number) {
    const media = audioRef.current,
      gain = gainRef.current;
    if (!media || !gain) return;
    try {
      gain.setLevel(value);
      media.muted = false;
      setVolume(value);
      setMuted(false);
      setError("");
      // The slider is a user gesture; resume here and on native play, including single-cue playback.
      resumeGain();
    } catch (e: unknown) {
      setError(ui("音量增益啟用失敗：") + String(e) + "");
    }
  }
  function timeline(full = false) {
    return (
      <div className="player-timeline">
        <time>{playbackTime(position)}</time>
        <input
          type="range"
          min="0"
          max={duration || 0}
          step="0.1"
          value={position}
          disabled={!duration}
          aria-label={full ? ui("完整播放器進度") : ui("播放進度")}
          aria-valuetext={`${playbackTime(position)} / ${playbackTime(duration)}`}
          style={
            {
              "--played": `${duration ? (position / duration) * 100 : 0}%`,
            } as CSSProperties
          }
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            dragging.current = true;
            setScrub(position);
          }}
          onChange={(e) => {
            const value = Number(e.target.value);
            if (dragging.current) setScrub(value);
            else seek(value);
          }}
          onPointerUp={(e) => {
            if (dragging.current) {
              dragging.current = false;
              setScrub(null);
              seek(Number(e.currentTarget.value));
            }
          }}
          onPointerCancel={() => {
            dragging.current = false;
            setScrub(null);
          }}
          onLostPointerCapture={() => {
            dragging.current = false;
            setScrub(null);
          }}
          onKeyDown={(e) => {
            if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
              e.preventDefault();
              seek(
                e.key === "Home"
                  ? 0
                  : e.key === "End"
                    ? duration
                    : position + (e.key === "ArrowLeft" ? -5 : 5),
              );
            }
          }}
        />
        <time>{playbackTime(duration)}</time>
      </div>
    );
  }
  function speed(full = false) {
    return (
      <label className="player-speed">
        <span className={full ? "" : "sr-only"}>{ui("播放倍速")}</span>
        <select
          aria-label={full ? ui("完整播放器倍速") : ui("播放倍速")}
          value={rate}
          onChange={(e) => setRate(Number(e.target.value))}
        >
          {playbackRates.map((value) => (
            <option key={value} value={value}>
              {value}×
            </option>
          ))}
        </select>
      </label>
    );
  }
  function transport(full = false) {
    return (
      <div className={"player-transport" + (full ? " full-transport" : "")}>
        <button
          title={ui("後退 5 秒")}
          aria-label={ui("後退 5 秒")}
          onClick={() => seek(position - 5)}
        >
          −5s
        </button>
        <button
          className="play-toggle"
          aria-label={playing ? ui("暫停") : ui("播放")}
          title={playing ? ui("暫停") : ui("播放")}
          onClick={toggle}
        >
          <PlayIcon playing={playing} />
        </button>
        <button
          title={ui("前進 5 秒")}
          aria-label={ui("前進 5 秒")}
          onClick={() => seek(position + 5)}
        >
          +5s
        </button>
      </div>
    );
  }
  function volumeControl() {
    return (
      <div className="player-volume">
        <button
          aria-label={muted ? ui("取消靜音") : ui("靜音")}
          aria-pressed={muted}
          onClick={() => {
            const media = audioRef.current;
            if (media) {
              media.muted = !muted;
              setMuted(!muted);
            }
          }}
        >
          {muted ? ui("靜音中") : ui("音量")}
        </button>
        <input
          type="range"
          min="0"
          max="2"
          step="0.05"
          value={muted ? 0 : volume}
          aria-label={ui("播放音量")}
          aria-valuetext={`${muted ? 0 : Math.round(volume * 100)}%`}
          title={ui("0–200%；超過 100% 使用音量增益")}
          onChange={(e) => changeVolume(Number(e.target.value))}
        />
        <output
          className={volume > 1 && !muted ? "boosted" : ""}
          aria-label={ui("目前音量")}
          title={volume > 1 && !muted ? ui("音量增益已啟用") : ui("音量")}
        >
          {muted ? 0 : Math.round(volume * 100)}%
        </output>
      </div>
    );
  }
  function playbackState() {
    return (
      <p className="player-status" role="status">
        {
          {
            paused: ui("已暫停"),
            playing: ui("播放中"),
            seeking: ui("跳轉中…"),
            buffering: ui("緩衝中…"),
          }[state]
        }
      </p>
    );
  }
  function playbackError(full = false) {
    return (
      error && (
        <div className={full ? "error" : "player-error"} role="alert">
          {error}
          <button
            onClick={() => {
              setError("");
              segmentRef.current = null;
              onSegmentEnd(null);
              transportRef.current?.retry();
            }}
          >
            {ui("重試播放")}
          </button>
        </div>
      )
    );
  }
  return (
    <>
      <audio
        ref={audioRef}
        hidden
        preload="metadata"
        src={source}
        onLoadedMetadata={() => {
          const media = audioRef.current;
          if (!media) return;
          setDuration(
            Number.isFinite(media.duration)
              ? media.duration
              : track.duration_ms / 1000,
          );
          media.playbackRate = rate;
          if (gainRef.current) gainRef.current.setLevel(volume);
          else media.volume = Math.min(1, volume);
          media.muted = muted;
          sync();
        }}
        onTimeUpdate={sync}
        onSeeked={sync}
        onPlay={() => setError("")}
        onPause={sync}
        onEnded={() => {
          segmentRef.current = null;
          onSegmentEnd(null);
          sync();
        }}
        onError={() =>
          setError(
            ui("音檔載入失敗") +
              (audioRef.current?.error?.message
                ? `：${audioRef.current.error.message}`
                : ui("，請確認原始音檔仍存在")) +
              "",
          )
        }
      />
      {!expanded && (
        <section
          ref={dock}
          className="player-dock"
          aria-label={ui("音軌播放器")}
          onClick={(e) => {
            if (
              !(
                e.target instanceof Element &&
                e.target.closest("button,input,select,label,a")
              )
            )
              onExpanded(true);
          }}
        >
          <div className="dock-top">
            <span className="dock-mark" aria-hidden="true">
              ♪
            </span>
            <button
              className="dock-title"
              aria-label={ui("展開完整播放器")}
              title={track.name}
              onClick={() => onExpanded(true)}
            >
              <strong>{track.name}</strong>
              <small>
                {segmentEnd === null
                  ? projectTitle
                  : ui("單句回聽 · 播放至句尾")}{" "}
                <span aria-hidden="true">↗</span>
              </small>
            </button>
            <div className="track-navigation">
              <button
                aria-label={ui("上一個音軌")}
                title={ui("上一個音軌")}
                disabled={switchingDisabled || trackIndex <= 0}
                onClick={() => onTrack(tracks[trackIndex - 1].id)}
              >
                {ui("上一軌")}
              </button>
              <button
                aria-label={ui("下一個音軌")}
                title={ui("下一個音軌")}
                disabled={switchingDisabled || trackIndex >= tracks.length - 1}
                onClick={() => onTrack(tracks[trackIndex + 1].id)}
              >
                {ui("下一軌")}
              </button>
              <button
                data-playlist-toggle
                aria-expanded={playlist}
                aria-controls="player-track-list"
                onClick={() => setPlaylist(!playlist)}
              >
                {ui("音軌清單")}
              </button>
            </div>
            {speed()}
          </div>
          {timeline()}
          <div className="dock-controls">
            {playbackState()}
            {transport()}
            {volumeControl()}
          </div>
          {playlist && (
            <div
              ref={listPanel}
              id="player-track-list"
              className="player-track-list"
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  e.stopPropagation();
                  setPlaylist(false);
                }
              }}
            >
              <label>
                {ui("搜尋音軌")}
                <input
                  autoFocus
                  value={trackQuery}
                  onChange={(e) => setTrackQuery(e.target.value)}
                />
              </label>
              <div>
                {tracks
                  .filter((t) =>
                    t.name.toLowerCase().includes(trackQuery.toLowerCase()),
                  )
                  .map((t) => (
                    <button
                      key={t.id}
                      disabled={switchingDisabled}
                      aria-current={t.id === track.id ? "true" : undefined}
                      onClick={() => {
                        onTrack(t.id);
                        setPlaylist(false);
                      }}
                    >
                      {t.id === track.id ? "▶ " : ""}
                      {t.name}
                    </button>
                  ))}
              </div>
            </div>
          )}
          {playbackError()}
        </section>
      )}
      {expanded && (
        <div
          className="modal-backdrop player-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget) onExpanded(false);
          }}
        >
          <section
            ref={sheet}
            className="player-sheet"
            role="dialog"
            aria-modal="true"
            aria-labelledby="full-player-title"
          >
            <div className="player-sheet-heading">
              <span>OWNSEI / PLAYER</span>
              <button
                ref={closeButton}
                aria-label={ui("收起完整播放器")}
                onClick={() => onExpanded(false)}
              >
                {ui("收起 ⌄")}
              </button>
            </div>
            <div className="player-track-heading">
              <div className="player-emblem" aria-hidden="true">
                ♪
              </div>
              <div>
                <p>{projectTitle}</p>
                <h2 id="full-player-title">{track.name}</h2>
                <small>{versionLabel}</small>
              </div>
            </div>
            <div className="player-subtitles" aria-label={ui("目前雙語字幕")}>
              <div>
                <span>{ui("日文原文")}</span>
                <p className="ja" lang="ja">
                  {subtitles.ja}
                </p>
              </div>
              <div>
                <span>{ui("中文字幕")}</span>
                <p className="zh" lang="zh-Hant">
                  {subtitles.zh}
                </p>
              </div>
            </div>
            {timeline(true)}
            {playbackState()}
            <div className="player-full-controls">
              {speed(true)}
              {transport(true)}
              {volumeControl()}
            </div>
            {segmentEnd !== null && (
              <div className="player-segment">
                <span>
                  {ui("單句回聽：至 ")}
                  {playbackTime(segmentEnd)}
                  {ui(" 暫停")}
                </span>
                <button onClick={() => onSegmentEnd(null)}>
                  {ui("改為連續播放")}
                </button>
              </div>
            )}
            {playbackError(true)}
            <details className="player-diagnostics">
              <summary>{ui("播放診斷紀錄")}</summary>
              <p className="muted">
                {ui(
                  "保留本音軌最近 120 筆播放事件；發生卡住時可複製，協助檢查跳轉、資料緩衝與解碼狀態。",
                )}
              </p>
              <button
                onClick={() =>
                  void navigator.clipboard
                    .writeText(
                      `${navigator.userAgent}\n${diagnostics.join("\n")}`,
                    )
                    .catch((e) =>
                      setError(ui("無法複製診斷：") + String(e) + ""),
                    )
                }
              >
                {ui("複製播放診斷")}
              </button>
              <pre>
                {navigator.userAgent}
                {"\n"}
                {diagnostics.join("\n") || ui("尚無播放事件")}
              </pre>
            </details>
            <details
              className="player-script"
              open={transcriptOpen}
              onToggle={(e) => setTranscriptOpen(e.currentTarget.open)}
            >
              <summary>
                {ui("雙語逐字稿 / ")}
                {cues.length}
                {ui(" 句")}
              </summary>
              <div className="player-script-tools">
                <small>{ui("點擊句段跳至該時間；使用目前選定的版本")}</small>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={follow}
                    onChange={(e) => onFollowPlayback(e.target.checked)}
                  />
                  {ui("跟隨播放")}
                </label>
              </div>
              <div className="player-script-list" ref={transcript}>
                {cues.length === 0 && (
                  <p className="muted">{ui("尚無逐字稿；仍可播放原音。")}</p>
                )}
                {cues.map((c) => (
                  <button
                    key={c.id}
                    ref={(el) => {
                      cueRows.current[c.id] = el;
                    }}
                    className={
                      "player-script-cue" +
                      (subtitles.ids.includes(c.id) ? " current" : "")
                    }
                    aria-label={
                      ui("跳至第 ") +
                      (c.ordinal + 1) +
                      ui(" 句，") +
                      playbackTime(c.start_ms / 1000) +
                      ""
                    }
                    aria-current={
                      subtitles.ids.includes(c.id) ? "true" : undefined
                    }
                    onClick={() => seek(c.start_ms / 1000)}
                  >
                    <time>{playbackTime(c.start_ms / 1000)}</time>
                    <span className="ja" lang="ja">
                      {c.ja || ""}
                    </span>
                    <span className="zh" lang="zh-Hant">
                      {c.zh || ""}
                    </span>
                  </button>
                ))}
              </div>
            </details>
          </section>
        </div>
      )}
    </>
  );
}
