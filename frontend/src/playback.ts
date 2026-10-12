export type PlaybackCue = {
  id: string;
  ordinal: number;
  start_ms: number;
  end_ms: number;
  ja: string;
  zh: string | null;
};

export const playbackRates = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

export function playbackTime(seconds: number) {
  const whole = Math.floor(Number.isFinite(seconds) ? Math.max(0, seconds) : 0);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor(whole / 60) % 60;
  return `${hours ? `${hours}:` : ""}${String(minutes).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

export function seekPosition(seconds: number, duration: number) {
  return Math.min(Math.max(0, Number.isFinite(duration) ? duration : 0), Math.max(0, Number.isFinite(seconds) ? seconds : 0));
}

export function currentSubtitles(cues: readonly PlaybackCue[], seconds: number) {
  const ms = seconds * 1000;
  const active = cues.filter(c => c.start_ms <= ms && ms < c.end_ms);
  return {
    ids: active.map(c => c.id),
    ja: active.map(c => c.ja || "").filter(Boolean).join("\n"),
    zh: active.map(c => c.zh || "").filter(Boolean).join("\n"),
  };
}
