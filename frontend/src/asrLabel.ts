import type {TranscriptVersion} from "./types";
import {t as ui} from "./i18n";

export function timingLabel(v:TranscriptVersion):string {
 const engine=v.segmentation?.engine;
 const threshold=v.segmentation?.settings?.threshold;
 const segmentation=engine?` · ${engine==="firered"?"FireRedVAD":engine==="silero"?"Silero VAD":engine}${typeof threshold==="number"?` ${threshold.toFixed(2)}`:""}`:"";
 const filter=v.use_vad?ui(" + 台詞篩選")+(v.asr_filter?` ${v.asr_filter.settings.threshold.toFixed(2)}`:""):"";
 return segmentation+filter;
}
