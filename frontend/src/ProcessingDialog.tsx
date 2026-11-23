import { type ExecutionOptions } from "./ExecutionSettings";
import { type AsrFilterOptions } from "./AsrFilterSettings";
import { t as ui } from "./i18n";
import { Modal } from "./Modal";
import type { TranslationOptions } from "./TranslationSettings";
import { ProcessingSettings } from "./ProcessingSettings";
import { missingProcessingModels } from "./processingOptions";
import type { ManagedModel } from "./ModelManager";
import type { TargetLanguage } from "./TranslationPreferences";
export function ProcessingDialog({
  mode,
  onMode,
  asrFilter,
  onAsrFilter,
  execution,
  onExecution,
  track,
  hasTranscript,
  asr,
  translation,
  options,
  catalog,
  target,
  busy,
  error,
  onAsr,
  onTranslation,
  onOptions,
  onTarget,
  onStyle,
  onStart,
  onClose,
}: {
  mode: string;
  execution: ExecutionOptions;
  onExecution: (v: ExecutionOptions) => void;
  asrFilter: AsrFilterOptions;
  onAsrFilter: (value: AsrFilterOptions) => void;
  onMode: (value: string) => void;
  track: string;
  hasTranscript: boolean;
  asr: string;
  translation: string;
  options: TranslationOptions;
  catalog: ManagedModel[];
  target: TargetLanguage;
  busy: boolean;
  error: string;
  onAsr: (v: string) => void;
  onTranslation: (v: string) => void;
  onOptions: (v: TranslationOptions) => void;
  onTarget: (v: TargetLanguage) => void;
  onStyle: () => void;
  onStart: (v: string) => void;
  onClose: () => void;
}) {
  const needed = missingProcessingModels(
    catalog,
    mode,
    asr,
    translation,
    execution,
    asrFilter,
  );
  return (
    <Modal
      title={ui("處理音軌")}
      onClose={onClose}
      busy={busy}
      className="processing-dialog"
    >
      <div className="processing-track">
        <small>{ui("目前音軌")}</small>
        <strong>{track}</strong>
      </div>
      <fieldset className="process-modes">
        <legend>{ui("處理方式")}</legend>
        {[
          ["run", ui("轉錄並翻譯")],
          ["transcribe", ui("只轉錄")],
          ["translate", ui("只翻譯")],
        ].map(([value, label]) => (
          <label key={value} className={mode === value ? "chosen" : ""}>
            <input
              type="radio"
              name="process-mode"
              value={value}
              checked={mode === value}
              disabled={busy || (value === "translate" && !hasTranscript)}
              onChange={() => onMode(value)}
            />
            {label}
          </label>
        ))}
      </fieldset>
      <ProcessingSettings
        mode={mode}
        asr={asr}
        onAsr={onAsr}
        translation={translation}
        onTranslation={onTranslation}
        options={options}
        onOptions={onOptions}
        execution={execution}
        onExecution={onExecution}
        asrFilter={asrFilter}
        onAsrFilter={onAsrFilter}
        catalog={catalog}
        disabled={busy}
      >
        <div className="preference-tools">
          <label>
            {ui("字幕語言")}
            <select
              disabled={busy}
              value={target}
              onChange={(e) => onTarget(e.target.value as TargetLanguage)}
            >
              <option value="zh-Hant">{ui("繁體中文")}</option>
              <option value="zh-Hans">{ui("简体中文")}</option>
            </select>
          </label>
          <button disabled={busy} onClick={onStyle}>
            {ui("翻譯風格指引")}
          </button>
        </div>
      </ProcessingSettings>
      <p>
        {mode === "translate"
          ? ui("使用目前選取的日文逐字稿，另存一份翻譯結果。")
          : ui("建立新的逐字稿，保留現有結果與人工校訂。")}
      </p>
      {needed.length > 0 && (
        <p className="error">
          {ui("請先到模型管理下載：")}
          {needed.map((m) => m.name).join("、")}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-footer">
        <button disabled={busy} onClick={onClose}>
          {ui("取消")}
        </button>
        <button
          className="primary"
          disabled={busy || !catalog.length || needed.length > 0}
          onClick={() => onStart(mode)}
        >
          {busy ? ui("提交中…") : ui("開始處理")}
        </button>
      </div>
    </Modal>
  );
}
