import { readPreference, writePreference } from "./interfacePreferences";
import { t as ui } from "./i18n";
import { useEffect, useState } from "react";
export type VadOptions = {
  threshold: number;
  min_speech_ms: number;
  min_silence_ms: number;
  pad_ms: number;
  max_region_ms: number;
  max_gain_db: number;
};
const sensitivePreset = (engine: string): VadOptions => ({
  threshold: engine === "silero" ? 0.35 : 0.3,
  min_speech_ms: 100,
  min_silence_ms: 500,
  pad_ms: 300,
  max_region_ms: 15000,
  max_gain_db: engine === "silero" ? 0 : 24,
});
export const vadDefaults = (engine: string): VadOptions => ({
  threshold: 0.4,
  min_speech_ms: 200,
  min_silence_ms: 900,
  pad_ms: 400,
  max_region_ms: 24000,
  max_gain_db: engine === "silero" ? 0 : 24,
});
export function loadVadOptions(): Record<string, VadOptions> {
  try {
    const saved = JSON.parse(
      readPreference("ownsei:vad-options:v1") || "{}",
    );
    return Object.fromEntries(
      ["firered", "silero"].map((k) => [k, { ...vadDefaults(k), ...saved[k] }]),
    );
  } catch {
    return { firered: vadDefaults("firered"), silero: vadDefaults("silero") };
  }
}
export function useVadOptions() {
  const [options, setOptions] = useState(loadVadOptions);
  useEffect(
    () =>
      writePreference("ownsei:vad-options:v1", JSON.stringify(options)),
    [options],
  );
  return [options, setOptions] as const;
}
export function VadControls({
  engine,
  value,
  onChange,
  disabled = false,
  showRegionLimit = true,
}: {
  engine: string;
  value: VadOptions;
  onChange: (v: VadOptions) => void;
  disabled?: boolean;
  showRegionLimit?: boolean;
}) {
  const change = (key: keyof VadOptions, n: number) =>
    onChange({ ...value, [key]: n });
  const presets = [
    ["較敏感", sensitivePreset(engine)],
    ["一般（預設）", vadDefaults(engine)],
    [
      "較保守",
      {
        ...sensitivePreset(engine),
        threshold: 0.65,
        min_speech_ms: 300,
        pad_ms: 200,
        max_gain_db: 0,
      },
    ],
  ] as const;
  return (
    <details className="vad-controls">
      <summary>
        {ui("語音偵測參數 · 門檻 ")}
        {Number(value.threshold).toFixed(2)}
      </summary>
      <p className="vad-hint">
        {ui(
          "提高門檻或最短語音可減少收進喘息與音效，但也較容易漏掉細語或短句。門檻過高時，句中較弱的語音可能被判為靜音，反而讓同一句台詞被切成更多片段。這是語音偵測設定，並非 Haas 的文字解碼參數。 ",
        )}
      </p>
      <div className="vad-presets">
        {presets.map(([label, preset]) => (
          <button
            type="button"
            key={label}
            disabled={disabled}
            onClick={() => onChange({ ...preset })}
          >
            {ui(label)}
          </button>
        ))}
        <button
          type="button"
          disabled={disabled}
          onClick={() => onChange(vadDefaults(engine))}
        >
          {ui("恢復預設 ")}
        </button>
      </div>
      <div className="vad-grid">
        <label>
          {ui("語音判定門檻 ")}
          <input
            aria-label={ui("語音判定門檻")}
            type="number"
            min="0.05"
            max="0.95"
            step="0.05"
            value={value.threshold}
            disabled={disabled}
            onChange={(e) => change("threshold", +e.target.value)}
          />
          <small>
            {ui(
              "提高數值會更嚴格；預設 ",
            )}
            {vadDefaults(engine).threshold.toFixed(2)}
          </small>
        </label>
        <label>
          {ui("最短語音（ms） ")}
          <input
            aria-label={ui("最短語音")}
            type="number"
            min="50"
            max="2000"
            step="50"
            value={value.min_speech_ms}
            disabled={disabled}
            onChange={(e) => change("min_speech_ms", +e.target.value)}
          />
          <small>
            {ui(
              "忽略比此長度更短的語音區段",
            )}
          </small>
        </label>
        <label>
          {ui("分句靜音（ms） ")}
          <input
            aria-label={ui("分句靜音")}
            type="number"
            min="100"
            max="3000"
            step="100"
            value={value.min_silence_ms}
            disabled={disabled}
            onChange={(e) => change("min_silence_ms", +e.target.value)}
          />
          <small>
            {ui(
              "提高數值可減少切得過碎的情況",
            )}
          </small>
        </label>
        <label>
          {ui("前後保留（ms） ")}
          <input
            aria-label={ui("前後保留")}
            type="number"
            min="0"
            max="1500"
            step="50"
            value={value.pad_ms}
            disabled={disabled}
            onChange={(e) => change("pad_ms", +e.target.value)}
          />
          <small>
            {ui("避免切掉句首與句尾")}
          </small>
        </label>
        {showRegionLimit && (
          <label>
            {ui("區段最長（秒） ")}
            <input
              aria-label={ui("區段最長秒數")}
              type="number"
              min="5"
              max="30"
              step="1"
              value={value.max_region_ms / 1000}
              disabled={disabled}
              onChange={(e) => change("max_region_ms", +e.target.value * 1000)}
            />
            <small>
              {ui(
                "限制單次送入轉錄的區段長度",
              )}
            </small>
          </label>
        )}
        {engine === "firered" && (
          <label>
            {ui("偵測最大增益（dB） ")}
            <input
              aria-label={ui("偵測最大增益")}
              type="number"
              min="0"
              max="24"
              step="3"
              value={value.max_gain_db}
              disabled={disabled}
              onChange={(e) => change("max_gain_db", +e.target.value)}
            />
            <small>
              {ui(
                "0 為關閉；只影響 VAD，不改原音或播放音量",
              )}
            </small>
          </label>
        )}
      </div>
    </details>
  );
}
