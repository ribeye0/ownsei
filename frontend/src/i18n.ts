import simplified from "./locales/zh-Hans.json";
export type Locale="zh-Hant"|"zh-Hans";
export function locale():Locale {
  return typeof localStorage!=="undefined" && localStorage.getItem("ownsei:locale")==="zh-Hans"?"zh-Hans":"zh-Hant";
}
// Only application-owned strings pass through this lookup. Audio names, notes,
// editable prompt text, transcripts and raw logs are never converted as UI labels.
export function t(text:string):string {
  return locale()==="zh-Hans"?(simplified as Record<string,string>)[text]??text:text;
}
export function setLocale(value:Locale) {
  localStorage.setItem("ownsei:locale",value);
  document.documentElement.lang=value==="zh-Hans"?"zh-CN":"zh-TW";
  window.dispatchEvent(new Event("ownsei:preferences"));
}
