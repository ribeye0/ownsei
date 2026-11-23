import {t as ui} from "./i18n";
import {switchTarget, type TranslationPreferences, type StyleVariants, type TargetLanguage} from "./translationPreferencesState";
export type {TranslationPreferences, StyleVariants, TargetLanguage} from "./translationPreferencesState";
export function TranslationPreferencesFields({ value, onChange, defaultStyles, disabled = false, compact = false, showTarget = true }: {
    value: TranslationPreferences; onChange: (v: TranslationPreferences) => void; defaultStyles: StyleVariants;
    disabled?: boolean; compact?: boolean; showTarget?: boolean;
}) {
    return <div className="translation-preferences">
    {showTarget && <label>{ui("字幕目標語言")}<select value={value.target_language} disabled={disabled} onChange={e => onChange(switchTarget(value, e.target.value as TargetLanguage, defaultStyles))}>
      <option value="zh-Hant">{ui("繁體中文")}</option><option value="zh-Hans">{ui("简体中文")}</option>
    </select></label>}
    <details open={compact?undefined:true}><summary>{ui("翻譯風格指引（展開可修改）")}</summary>
    <label>{ui("翻譯風格指引")}<textarea rows={8} maxLength={2000} value={value.translation_style} disabled={disabled} onChange={e => onChange({ ...value, translation_style: e.target.value })}/></label>
    <div className="preference-tools"><small>{value.translation_style.length}{ui("/2000 · 修改只用於新翻譯，舊稿與人工校對保留")}</small>
      <button disabled={disabled} onClick={() => onChange({ ...value, translation_style: defaultStyles[value.target_language] })}>{ui("恢復預設風格")}</button></div>
    </details>
    <small>{ui("繁體與簡體各自保留風格。可調整口語與用詞偏好；輸出格式及逐句對應由程式管理，角色設定與譯名請填專案備註及術語表。")}</small>
  </div>;
}
