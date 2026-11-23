import { readPreference, savePreference } from "./interfacePreferences";
export type ExportPreferences = {
  languages: string[];
  formats: string[];
  chinese: string;
  languageTag: boolean;
  destination: string;
  overwrite: boolean;
};
const key = "ownsei:export-preferences:v1";
const languages = ["ja", "zh", "bilingual"];
const formats = ["vtt", "srt", "lrc", "ass", "txt", "jsonl"];
function choices(value: unknown, allowed: string[], fallback: string[]): string[] {
  const valid = Array.isArray(value) ? [...new Set(value.filter((v): v is string => typeof v === "string" && allowed.includes(v)))] : [];
  return valid.length ? valid : [...fallback];
}
export function normalizeExportPreferences(value: unknown): ExportPreferences {
  const v = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  return {
    languages: choices(v.languages, languages, ["zh"]),
    formats: choices(v.formats, formats, ["vtt"]),
    chinese: typeof v.chinese === "string" && ["as-result", "zh-Hant", "zh-Hans"].includes(v.chinese) ? v.chinese : "as-result",
    languageTag: v.languageTag === true,
    destination: typeof v.destination === "string" && ["download", "source", "sub"].includes(v.destination) ? v.destination : "download",
    overwrite: v.overwrite === true,
  };
}
export function loadExportPreferences(): ExportPreferences {
  try {
    return normalizeExportPreferences(JSON.parse(readPreference(key) || "null"));
  } catch {
    return normalizeExportPreferences(null);
  }
}
export function saveExportPreferences(value: ExportPreferences): Promise<void> {
  return savePreference(key, JSON.stringify(normalizeExportPreferences(value)));
}
