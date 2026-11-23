import type { ReactNode } from "react";
import { t as ui } from "./i18n";
import { ExecutionSettings, type ExecutionOptions } from "./ExecutionSettings";
import { AsrFilterSettings, type AsrFilterOptions } from "./AsrFilterSettings";
import {
  TranslationSettings,
  translationDefaults,
  type TranslationOptions,
} from "./TranslationSettings";
import type { ManagedModel } from "./ModelManager";

export type ProcessingSettingsProps = {
  mode: string;
  asr: string;
  onAsr: (value: string) => void;
  translation: string;
  onTranslation: (value: string) => void;
  options: TranslationOptions;
  onOptions: (value: TranslationOptions) => void;
  execution: ExecutionOptions;
  onExecution: (value: ExecutionOptions) => void;
  asrFilter: AsrFilterOptions;
  onAsrFilter: (value: AsrFilterOptions) => void;
  catalog: ManagedModel[];
  disabled: boolean;
  children?: ReactNode;
};

/** Shared by single-track and batch processing; language/style tools remain caller-owned. */
export function ProcessingSettings({
  mode,
  asr,
  onAsr,
  translation,
  onTranslation,
  options,
  onOptions,
  execution,
  onExecution,
  asrFilter,
  onAsrFilter,
  catalog,
  disabled,
  children,
}: ProcessingSettingsProps) {
  return (
    <>
      {mode !== "translate" && (
        <label>
          {ui("轉錄模型")}
          <select
            disabled={disabled}
            value={asr}
            onChange={(e) => onAsr(e.target.value)}
          >
            {catalog
              .filter((m) => m.role === "asr")
              .map((m) => (
                <option key={m.id} value={m.id}>
                  {ui(m.name)}
                </option>
              ))}
          </select>
        </label>
      )}
      <ExecutionSettings
        value={execution}
        onChange={onExecution}
        mode={mode}
        disabled={disabled}
      />
      {mode !== "translate" && (
        <AsrFilterSettings
          execution={execution}
          onExecution={onExecution}
          value={asrFilter}
          onChange={onAsrFilter}
          disabled={disabled}
        />
      )}
      {mode !== "transcribe" && (
        <>
          <label>
            {ui("翻譯模型")}
            <select
              disabled={disabled}
              value={translation}
              onChange={(e) => {
                onTranslation(e.target.value);
                onOptions(translationDefaults(e.target.value));
              }}
            >
              {catalog
                .filter((m) => m.role === "translation")
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {ui(m.name)}
                  </option>
                ))}
            </select>
          </label>
          <TranslationSettings
            value={options}
            onChange={onOptions}
            disabled={disabled}
          />
          {children}
        </>
      )}
    </>
  );
}
