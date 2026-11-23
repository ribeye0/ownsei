import type { PlaybackCue } from "./playback";

// In a silent gap keep the preceding line; before the first line show its start.
export function followCue(
  cues: readonly PlaybackCue[],
  seconds: number,
): string | undefined {
  if (!Number.isFinite(seconds)) return undefined;
  const ms = seconds * 1000;
  let current: PlaybackCue | undefined,
    previous: PlaybackCue | undefined,
    first: PlaybackCue | undefined;
  for (const cue of cues) {
    if (!first || cue.start_ms < first.start_ms) first = cue;
    if (cue.start_ms <= ms && (!previous || cue.start_ms >= previous.start_ms))
      previous = cue;
    if (
      cue.start_ms <= ms &&
      ms < cue.end_ms &&
      (!current || cue.start_ms >= current.start_ms)
    )
      current = cue;
  }
  return (current || previous || first)?.id;
}

// Scroll only the transcript's own viewport, leaving the page and player fixed.
export function followScrollTop(
  scrollTop: number,
  height: number,
  rowTop: number,
  rowHeight: number,
): number | undefined {
  if (
    ![scrollTop, height, rowTop, rowHeight].every(Number.isFinite) ||
    height <= 0
  )
    return undefined;
  if (rowTop >= 0 && rowTop + rowHeight <= height) return undefined;
  if (rowTop <= 0 && rowTop + rowHeight >= height) return undefined;
  return Math.max(
    0,
    scrollTop + rowTop - Math.max(0, (height - rowHeight) / 2),
  );
}
