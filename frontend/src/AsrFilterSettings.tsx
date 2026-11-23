import { readPreference, writePreference } from "./interfacePreferences";
import { useEffect, useState } from "react";
import { VadControls, useVadOptions, type VadOptions } from "./VadControls";
import "./vad-controls.css";
import { t as ui } from "./i18n";
import type { ExecutionOptions } from "./ExecutionSettings";
export type AsrFilterOptions = {
  enabled: boolean;
  settings: VadOptions;
  min_overlap: number;
};
export function useAsrFilter() {
  const [vad, setVad] = useVadOptions();
  const [prefs, setPrefs] = useState(() => {
    try {
      return {
        enabled: false,
        min_overlap: 0.25,
        ...JSON.parse(readPreference("ownsei:asr-filter:v1") || "{}"),
      };
    } catch {
      return { enabled: false, min_overlap: 0.25 };
    }
  });
  useEffect(
    () => writePreference("ownsei:asr-filter:v1", JSON.stringify(prefs)),
    [prefs],
  );
  const value: AsrFilterOptions = { ...prefs, settings: vad.firered };
  const change = (v: AsrFilterOptions) => {
    setPrefs({ enabled: v.enabled, min_overlap: v.min_overlap });
    setVad({ ...vad, firered: v.settings });
  };
  return [value, change] as const;
}
export function AsrFilterSettings({
  value,
  onChange,
  execution,
  onExecution,
  disabled = false,
}: {
  value: AsrFilterOptions;
  onChange: (v: AsrFilterOptions) => void;
  execution: ExecutionOptions;
  onExecution: (v: ExecutionOptions) => void;
  disabled?: boolean;
}) {
  const engine = execution.vad_engine;
  return (
    <section className="asr-segmentation">
      <label>
        {ui("轉錄前語音切分")}
        <select
          disabled={disabled}
          value={engine}
          onChange={(e) =>
            onExecution({ ...execution, vad_engine: e.target.value })
          }
        >
          <option value="firered">FireRedVAD · CPU</option>
          <option value="silero">Silero VAD v6.2 · CPU</option>
          <option value="none">{ui("不使用 VAD（固定時間窗）")}</option>
        </select>
      </label>
      <small>
        {ui(
          "VAD 決定送入 Haas 的音訊範圍；時間戳由 Haas 原生輸出。不使用 VAD 可避免因偵測漏判而遺漏台詞",
        )}
      </small>
      {engine !== "none" && (
        <VadControls
          engine={engine}
          value={{
            ...value.settings,
            ...(engine === "silero" ? { max_gain_db: 0 } : {}),
          }}
          onChange={(settings) => onChange({
            ...value,
            settings: engine === "silero"
              ? { ...settings, max_gain_db: value.settings.max_gain_db }
              : settings,
          })}
          disabled={disabled}
        />
      )}
      <label className="vad-filter-enable">
        <input
          aria-label={ui("啟用 FireRedVAD 台詞篩選")}
          type="checkbox"
          checked={value.enabled}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
        />
        {ui("啟用 FireRedVAD 台詞篩選")}
      </label>
      {value.enabled && (
        <>
          <p className="vad-hint">
            {ui(
              "辨識後額外排除缺乏語音支持的整句，保留原始辨識與篩選紀錄。提高語音占比可能漏掉短句",
            )}
          </p>
          {engine === "none" && (
            <VadControls
              engine="firered"
              value={value.settings}
              onChange={(settings) => onChange({ ...value, settings })}
              disabled={disabled}
              showRegionLimit={false}
            />
          )}
          <label className="vad-filter-overlap">
            {ui("台詞語音占比下限（%）")}
            <input
              aria-label={ui("台詞語音占比下限")}
              type="number"
              min="0"
              max="100"
              step="5"
              value={Math.round(value.min_overlap * 100)}
              disabled={disabled}
              onChange={(e) =>
                onChange({ ...value, min_overlap: +e.target.value / 100 })
              }
            />
          </label>
        </>
      )}
    </section>
  );
}
