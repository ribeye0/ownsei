import { type ExecutionOptions } from "./ExecutionSettings";
import { type AsrFilterOptions } from "./AsrFilterSettings";
import { t as ui } from "./i18n";
import type { TranslationOptions } from "./TranslationSettings";
import { ProcessingSettings } from "./ProcessingSettings";
import { missingProcessingModels } from "./processingOptions";
import type { ManagedModel } from "./ModelManager";
import { useEffect, useState } from "react";
import { api } from "./api";
import type { Track, TranscriptVersion } from "./types";
import { Modal } from "./Modal";
import {
  TranslationPreferencesFields,
  type TranslationPreferences,
  type StyleVariants,
} from "./TranslationPreferences";
import { resultStamp } from "./resultInfo";
import { timingLabel } from "./asrLabel";
export function BatchDialog({
  base,
  tracks,
  current,
  asr,
  translation,
  translationOptions,
  asrFilter,
  onAsrFilter,
  execution,
  onExecution,
  catalog,
  preferences,
  defaultStyles,
  models,
  onClose,
  onSubmitted,
}: {
  base: string;
  tracks: Track[];
  current: string;
  asr: string;
  translation: string;
  translationOptions: TranslationOptions;
  execution: ExecutionOptions;
  onExecution: (v: ExecutionOptions) => void;
  asrFilter: AsrFilterOptions;
  onAsrFilter: (value: AsrFilterOptions) => void;
  catalog: ManagedModel[];
  preferences: TranslationPreferences;
  defaultStyles: StyleVariants;
  models: Record<string, string>;
  onClose: () => void;
  onSubmitted: () => Promise<void>;
}) {
  const [ids, setIds] = useState<string[]>(current ? [current] : []),
    [mode, setMode] = useState("run");
  const [asrModel, setAsr] = useState(asr),
    [mt, setMt] = useState(translation);
  const [prefs, setPrefs] = useState(preferences),
    [versions, setVersions] = useState<Record<string, TranscriptVersion[]>>({}),
    [sources, setSources] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [native, setNative] = useState(translationOptions);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all(
      tracks.map(
        async (track) =>
          [
            track.id,
            await api<TranscriptVersion[]>(
              `${base}/tracks/${track.id}/versions`,
              "GET",
              undefined,
              { signal: controller.signal },
            ),
          ] as const,
      ),
    )
      .then((rows) => {
        if (!controller.signal.aborted) {
          setVersions(Object.fromEntries(rows));
          setSources(
            Object.fromEntries(
              tracks.map((t) => [t.id, t.active_version || ""]),
            ),
          );
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(String(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [base]);
  const needed = missingProcessingModels(
    catalog,
    mode,
    asrModel,
    mt,
    execution,
    asrFilter,
  );
  const eligible = tracks.filter((t) => mode !== "translate" || sources[t.id]);
  const selected = ids.filter((id) => eligible.some((t) => t.id === id));
  async function submit() {
    if (needed.length || !catalog.length || !selected.length) return;
    setBusy(true);
    setError("");
    try {
      await api(base + "/jobs", "POST", {
        action: mode,
        track_ids: selected,
        asr: asrModel,
        translation: mt,
        continue_on_error: true,
        fresh_asr: mode !== "translate",
        target_language: prefs.target_language,
        translation_style: prefs.translation_style,
        translation_options: native,
        execution,
        asr_filter: {
          ...asrFilter,
          enabled: asrModel.startsWith("haas-ja-v1.0") && asrFilter.enabled,
        },
        ...(mode === "translate"
          ? {
              transcript_versions: Object.fromEntries(
                selected.map((id) => [id, sources[id]]),
              ),
            }
          : {}),
      });
      await onSubmitted();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={ui("批量處理")}
      onClose={onClose}
      busy={busy}
      className="batch-dialog"
    >
      <label>
        {ui("處理模式")}
        <select
          disabled={busy}
          value={mode}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="run">{ui("轉錄＋翻譯（建立新結果）")}</option>
          <option value="transcribe">{ui("只轉錄（建立新逐字稿）")}</option>
          <option value="translate">{ui("只翻譯（使用既有轉錄）")}</option>
        </select>
      </label>
      <div className="batch-tools">
        <button
          disabled={busy || loading}
          onClick={() => setIds(eligible.map((t) => t.id))}
        >
          {ui("全選")}
        </button>
        <button disabled={busy} onClick={() => setIds([])}>
          {ui("取消全選")}
        </button>
        <span>
          {ui("已選 ")}
          {selected.length}/{tracks.length}
          {ui(" 條音軌")}
        </span>
      </div>
      <div className="batch-tracks">
        {tracks.map((track) => (
          <div className="batch-track" key={track.id}>
            <label className="check">
              <input
                type="checkbox"
                disabled={busy || (mode === "translate" && !sources[track.id])}
                checked={selected.includes(track.id)}
                onChange={(e) =>
                  setIds(
                    e.target.checked
                      ? [...ids, track.id]
                      : ids.filter((id) => id !== track.id),
                  )
                }
              />
              <span>{track.name}</span>
            </label>
            {mode === "translate" && (
              <label>
                {ui("使用轉錄")}
                <select
                  disabled={busy || loading || !versions[track.id]?.length}
                  value={sources[track.id] || ""}
                  onChange={(e) =>
                    setSources({ ...sources, [track.id]: e.target.value })
                  }
                >
                  {!sources[track.id] && (
                    <option value="">{ui("尚無轉錄，請先轉錄")}</option>
                  )}
                  {(versions[track.id] || []).map((v) => (
                    <option key={v.version} value={v.version}>
                      {models[v.model || ""] || ui("匯入")}
                      {timingLabel(v)}
                      {v.extra_alignment ? ui(" + 對齊") : ""} ·{" "}
                      {resultStamp(v.created_at, v.created_at_estimated)}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        ))}
      </div>
      <ProcessingSettings
        mode={mode}
        asr={asrModel}
        onAsr={setAsr}
        translation={mt}
        onTranslation={setMt}
        options={native}
        onOptions={setNative}
        execution={execution}
        onExecution={onExecution}
        asrFilter={asrFilter}
        onAsrFilter={onAsrFilter}
        catalog={catalog}
        disabled={busy}
      >
        <TranslationPreferencesFields
          value={prefs}
          onChange={setPrefs}
          defaultStyles={defaultStyles}
          disabled={busy}
          compact
        />
      </ProcessingSettings>
      <p>
        {ui(
          "依音軌順序處理；單軌內容失敗會繼續其他音軌，模型或環境錯誤則停止。此處風格只用於本次任務，角色備註與術語表沿用專案設定",
        )}
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
      <button
        className="primary"
        disabled={
          busy ||
          loading ||
          !selected.length ||
          !catalog.length ||
          needed.length > 0 ||
          (mode !== "transcribe" && !prefs.translation_style.trim())
        }
        onClick={() => void submit()}
      >
        {busy
          ? ui("提交中…")
          : loading
            ? ui("讀取來源…")
            : ui("開始處理 ") + selected.length + ui(" 條音軌")}
      </button>
    </Modal>
  );
}
