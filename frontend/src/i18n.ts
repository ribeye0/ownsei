import simplified from "./locales/zh-Hans.json";
export type Locale = "zh-Hant" | "zh-Hans";
export type LocalePreference = Locale | "auto";
let sessionPreference: LocalePreference | undefined;

// Browsers expose language preferences, not the OS language directly. Explicit
// script tags take precedence over regions; skip non-Chinese preferences.
export function systemLocale(languages: readonly string[]): Locale {
  for (const language of languages) {
    const parts = language.toLowerCase().replace(/_/g, "-").split("-");
    if (parts[0] !== "zh") continue;
    if (parts.includes("hant") || parts.includes("cht")) return "zh-Hant";
    if (parts.includes("hans") || parts.includes("chs")) return "zh-Hans";
    return parts.some(part => ["tw", "hk", "mo"].includes(part)) ? "zh-Hant" : "zh-Hans";
  }
  return "zh-Hant";
}
export function localePreference(): LocalePreference {
  if (sessionPreference !== undefined) return sessionPreference;
  try {
    const value = localStorage.getItem("ownsei:locale");
    return value === "zh-Hans" || value === "zh-Hant" ? value : "auto";
  } catch {
    return "auto";
  }
}
export function locale(): Locale {
  const preference = localePreference();
  if (preference !== "auto") return preference;
  return systemLocale(typeof navigator === "undefined" ? [] : navigator.languages?.length ? navigator.languages : [navigator.language]);
}
// Only application-owned strings pass through this lookup. Audio names, notes,
// editable prompt text, transcripts and raw logs are never converted as UI labels.
export function t(text: string): string {
  return locale() === "zh-Hans" ? (simplified as Record<string, string>)[text] ?? text : text;
}
export function setLocale(value: LocalePreference) {
  try {
    localStorage.setItem("ownsei:locale", value);
    sessionPreference = undefined;
  } catch {
    sessionPreference = value;
  }
  document.documentElement.lang = locale() === "zh-Hans" ? "zh-CN" : "zh-TW";
  window.dispatchEvent(new Event("ownsei:preferences"));
}
