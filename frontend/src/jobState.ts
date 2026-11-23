export const liveJob = (status: string) =>
  ["queued", "waiting", "running", "cancelling"].includes(status);
export const finishedJobs = (jobs: { id: string; status: string }[]) =>
  jobs
    .filter((j) => !liveJob(j.status))
    .map((j) => j.id + j.status)
    .join(",");

import type { Job } from "./JobPanel";
const bounded = (value: number) =>
  Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
/** Each stage spans all selected tracks. Read legacy weighted counters only as a fallback. */
export function stageProgress(job: Job) {
  const names =
    job.action === "run"
      ? ["transcribe", "translate"]
      : ["transcribe", "translate", "export"].includes(job.action)
        ? [job.action]
        : [];
  const ids = [
    ...new Set([
      ...(job.tracks || []).map((t) => t.id),
      ...(job.options?.track_ids || []),
      ...Object.keys(job.stages || {}),
    ]),
  ];
  const total = Math.max(1, job.tracks_total || ids.length);
  const current =
    job.current_track ||
    job.tracks?.[(job.track_number || 1) - 1]?.id ||
    (ids.length === 1 ? ids[0] : "");
  const weights: Record<string, number> =
    job.action === "run"
      ? { transcribe: 0.55, translate: 0.4, export: 0.05 }
      : job.action === "export"
        ? { export: 1 }
        : { [job.action]: 0.95, export: 0.05 };
  return names.map((name) => {
    let done = 0,
      sum = 0,
      failed = false,
      running = false,
      cancelled = false;
    for (const id of ids) {
      const record = job.stages?.[id]?.[name];
      if (record?.status === "completed") {
        done++;
        sum++;
        continue;
      }
      failed ||=
        record?.status === "failed" || record?.status === "interrupted";
      cancelled ||= record?.status === "cancelled";
      running ||= record?.status === "running" && liveJob(job.status);
      if (typeof record?.progress === "number") sum += bounded(record.progress);
      else if (
        id === current &&
        job.current_stage === name &&
        typeof job.track_progress === "number"
      ) {
        const entries = Object.entries(weights),
          offset = entries
            .slice(
              0,
              entries.findIndex(([stage]) => stage === name),
            )
            .reduce((n, [, v]) => n + v, 0);
        sum += bounded((job.track_progress - offset) / (weights[name] || 1));
      }
    }
    // Legacy standalone jobs may have only the aggregate counter.
    if (!ids.length && names.length === 1) sum = bounded(job.progress);
    const complete =
      done === total || (!ids.length && job.status === "completed");
    return {
      name,
      value: complete ? 1 : bounded(sum / total),
      done: complete ? total : done,
      total,
      status: complete
        ? "completed"
        : failed
          ? "failed"
          : cancelled
            ? "cancelled"
            : running
              ? "running"
              : "pending",
    };
  });
}
