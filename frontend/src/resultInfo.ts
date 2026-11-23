export function resultStamp(createdAt?: number | null, estimated = false): string {
  if (!createdAt) return "時間未記錄";
  const d = new Date(createdAt * 1000), pad = (n: number) => String(n).padStart(2, "0");
  return `${estimated ? "約 " : ""}${pad(d.getFullYear() % 100)}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function issueReport(context: {
  projectTitle: string; trackName: string; transcriptLabel: string; translationLabel: string;
  location: {project: string; track: string; transcript: string | null; translation: string | null; cue: string | null};
  cue?: {ordinal: number; start_ms: number; end_ms: number; ja: string; zh: string | null; provenance: object};
}, options:{full?:boolean;code?:string}={full:true}): string {
  const c = context.cue;
  return [
    "[Ownsei 問題定位]", `專案：${context.projectTitle}`, `音軌：${context.trackName}`,
    `轉錄：${context.transcriptLabel}`, `翻譯：${context.translationLabel}`,
    ...(c ? [`句段：#${c.ordinal + 1} · ${(c.start_ms / 1000).toFixed(3)}–${(c.end_ms / 1000).toFixed(3)} 秒`,
      `日文：${c.ja}`, `中文：${c.zh || "（無）"}`] : []),
    options.code && !options.full ? `定位碼：${options.code}` : `定位ID：${JSON.stringify(context.location)}`,
    ...(c && options.full ? [`來源與時間診斷：${JSON.stringify(c.provenance)}`] : []),
    "問題描述：（請在這裡補充）",
  ].join("\n");
}
