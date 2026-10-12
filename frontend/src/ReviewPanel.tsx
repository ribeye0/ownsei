import {t as ui} from "./i18n";
import { useEffect, useState } from "react";
import { api } from "./api";
import type { Cue, TranslationVersion } from "./types";
type RecordRow = {
    id: string;
    kind: string;
    reason: string;
    evidence_ids: string[];
    before?: string;
    text?: string;
    applied?: boolean;
};
type ReviewData = {
    stage?: string;
    status?: string;
    parent_result_id?: string;
    coverage?: string[];
    patches?: RecordRow[];
    issues?: RecordRow[];
    evidence?: Record<string, {
        ordinal: number;
        ja: string;
        zh?: string;
    }>;
    reference?: {
        kind: string;
        text: string;
        quote: string;
        evidence_ids: string[];
        confirmed: boolean;
    }[];
};
export function ReviewPanel({ base, trackId, result, cues }: {
    base: string;
    trackId: string;
    result?: TranslationVersion;
    cues: Cue[];
}) {
    const [data, setData] = useState<ReviewData | null>(null), [error, setError] = useState("");
    useEffect(() => {
        setData(null);
        setError("");
        if (!result)
            return;
        const abort = new AbortController();
        api<ReviewData>(`${base}/tracks/${trackId}/translations/${result.id}/review`, "GET", undefined, { signal: abort.signal })
            .then(value => { if (!abort.signal.aborted)
            setData(value); })
            .catch(e => { if (!abort.signal.aborted)
            setError(String(e)); });
        return () => abort.abort();
    }, [base, trackId, result?.id, result?.reviewed, result?.patch_count, result?.issue_count, result?.status]);
    if (!result || (result.stage !== "review" && !data?.reference?.length))
        return null;
    const location = (id: string) => `#${(data?.evidence?.[id]?.ordinal ?? cues.find(c => c.id === id)?.ordinal ?? -1) + 1}`;
    const source = (id: string) => data?.evidence?.[id]?.ja ?? cues.find(c => c.id === id)?.ja;
    const evidence = (ids: string[]) => <details><summary>{ui("查看原文依據 ")}{ids.map(location).join("、")}</summary>
    {ids.map(id => <div className="review-entry" key={id}><b>{location(id)}</b><p lang="ja">{source(id)}</p>
      {data?.evidence?.[id]?.zh && <p>{ui("來源譯文：")}{data.evidence[id].zh}</p>}</div>)}
  </details>;
    return <section className="review-panel" aria-label={ui("審校修改與全文參照")}>
    {result.stage === "review" && <details>
      <summary>{ui("審校紀錄 · ")}{result.reviewed || 0}/{cues.length}{ui(" 句 · 修改 ")}{result.patch_count || 0}{ui(" · 疑點 ")}{result.issue_count || 0}</summary>
      <p>{ui("來源初譯：")}{data?.parent_result_id || "—"}{ui("。完整審校代表已覆蓋全部句段，仍可人工校對。")}</p>
      {error && <p role="alert">{error}</p>}
      {(data?.patches || []).map((p, i) => <div className="review-entry" key={`${p.id}:${i}`}>
        <b>{location(p.id)} · {p.applied ? ui("已修改") : ui("保留原稿／建議")}</b>
        <p lang="ja">{source(p.id)}</p>
        <div className="review-diff"><span>{p.before}</span><span>→ {p.text}</span></div>
        <small>{p.reason}{ui(" · 依據 ")}{p.evidence_ids.map(location).join("、")}</small>
        {evidence(p.evidence_ids)}
      </div>)}
      {(data?.issues || []).map((p, i) => <div className="review-entry" key={`issue:${i}`}><b>{location(p.id)}{ui(" · 疑點")}</b><p>{p.reason}</p>{evidence(p.evidence_ids)}</div>)}
      {data && !data.patches?.length && !data.issues?.length && <p>{ui("目前沒有修改或疑點紀錄")}</p>}
    </details>}
    {!!data?.reference?.length && <details><summary>{ui("全文參照 · ")}{data.reference.length}{ui(" 項機器參考")}</summary>
      <p>{ui("由原文整理，包含未確認資訊；使用者術語與備註優先")}</p>
      {data.reference.map((f, i) => <div className="review-entry" key={i}><p>{f.text}</p><small>{ui("原文：")}{f.quote} · {f.evidence_ids.map(location).join("、")}</small></div>)}
    </details>}
  </section>;
}
