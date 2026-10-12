import type {Cue} from "./types";

export function editRequest(original:Cue, draft:Cue) {
  return {revision:original.revision, translation_version:original.provenance.translation_version ?? null,
    changes:{ja_override:draft.ja===original.ja ? original.ja_override : draft.ja,
      zh_override:draft.zh===original.zh ? original.zh_override : draft.zh,
      start_ms:draft.start_ms, end_ms:draft.end_ms, review_flags:draft.review_flags}};
}
export function dirtyDraft(original:Cue|null, draft:Cue|null) {
  if (!original || !draft) return false;
  return JSON.stringify(editRequest(original,draft).changes)!==JSON.stringify(editRequest(original,original).changes);
}
