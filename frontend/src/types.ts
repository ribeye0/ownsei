import type {Job} from "./JobPanel";
export type Track = {
  id: string;
  name: string;
  source: string;
  duration_ms: number;
  active_version: string | null;
};
export type Cue = {
  id: string;
  ordinal: number;
  start_ms: number;
  end_ms: number;
  ja_raw: string;
  ja: string;
  zh: string | null;
  ja_override: string | null;
  zh_override: string | null;
  revision: number;
  review_flags: string[];
  timing_status: string;
  translation_status: string;
  provenance: {alignment_diagnostic?: Record<string, unknown>; translation_version?: string};
};
export type Details = {
  settings: { title: string; notes: string; glossary: Record<string, string>; target_language?:"zh-Hant"|"zh-Hans"; translation_style?:string; translation_styles?:Record<"zh-Hant"|"zh-Hans",string>; glossary_entries?:import("./terminology").TermEntry[] };
  tracks: Track[];
  jobs: Job[];
  path: string;
};
export type TranscriptVersion = {
  version: string;
  cues: number;
  model?: string;
  limit_ms?: number | null;
  extra_alignment?: boolean;
  use_vad?: boolean;
  segmentation?:{engine:string;settings?:{threshold?:number}};
  asr_filter?:{settings:{threshold:number};min_overlap:number};
  vad_fallback?: boolean;
  created_at?: number | null;
  created_at_estimated?: boolean;
};
export type TranslationVersion = { id: string; model: string; created_at: number; active: boolean; translated: number; manual: number; stale: number;
  target_language?:"zh-Hant"|"zh-Hans"; translation_style?:string; stage?:"draft"|"review"; scope?:"full"|"selected"; status?:string; parent_result_id?:string|null; reviewed?:number; patch_count?:number; issue_count?:number };
export type TrackView = {track:Track; cues:Cue[]; versions:TranscriptVersion[]; translations:TranslationVersion[]; files:string[]};
