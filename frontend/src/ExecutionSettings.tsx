import { useEffect, useState } from "react";
import { api } from "./api";
import { t as ui } from "./i18n";
export type ExecutionOptions = {
  asr_device: string;
  llm_device: string;
  vad_engine: string;
  whisper_device: string;
};
const defaults: ExecutionOptions = {
  asr_device: "auto",
  llm_device: "auto",
  vad_engine: "firered",
  whisper_device: "cpu",
};
type Inventory = Record<
  "speech" | "llama",
  { devices: { id: string; name: string }[]; error: string | null }
>;
export function useExecution() {
  const [value, setValue] = useState<ExecutionOptions>(() => {
    try {
      return {
        ...defaults,
        ...JSON.parse(
          localStorage.getItem("ownsei:vulkan:execution:v1") || "{}",
        ),
      };
    } catch {
      return defaults;
    }
  });
  useEffect(
    () =>
      localStorage.setItem("ownsei:vulkan:execution:v1", JSON.stringify(value)),
    [value],
  );
  return [value, setValue] as const;
}
export function ExecutionSettings({
  value,
  onChange,
  mode,
  asr,
  disabled = false,
}: {
  value: ExecutionOptions;
  onChange: (v: ExecutionOptions) => void;
  mode: string;
  asr: string;
  disabled?: boolean;
}) {
  const [inventory, setInventory] = useState<Inventory | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void api<Inventory>("/devices", "GET", undefined, {
      signal: controller.signal,
      timeoutMs: 30000,
    })
      .then(setInventory)
      .catch((e) => {
        if (!controller.signal.aborted) setError(String(e));
      });
    return () => controller.abort();
  }, []);
  const select = (
    key: "asr_device" | "llm_device",
    engine: "speech" | "llama",
    label: string,
  ) => (
    <label>
      {ui(label)}
      <select
        disabled={disabled}
        value={value[key]}
        onChange={(e) => onChange({ ...value, [key]: e.target.value })}
      >
        <option value="auto">{ui("自動（優先 GPU）")}</option>
        {inventory?.[engine].devices.map((d) => (
          <option value={d.id} key={d.id}>
            {d.id} · {d.name}
          </option>
        ))}
        {value[key] !== "auto" &&
          !inventory?.[engine].devices.some((d) => d.id === value[key]) && (
            <option value={value[key]}>
              {value[key]} · {ui("尚未確認可用")}
            </option>
          )}
      </select>
      {inventory?.[engine].error && (
        <small className="error">{inventory[engine].error}</small>
      )}
    </label>
  );
  return (
    <fieldset className="execution-settings">
      <legend>{ui("運算裝置")}</legend>
      {mode !== "translate" &&
        (asr.startsWith("haas-") ? (
          select("asr_device", "speech", "轉錄裝置（speech.cpp）")
        ) : (
          <label>
            {ui("Whisper 運算裝置")}
            <select
              disabled={disabled}
              value={value.whisper_device}
              onChange={(e) =>
                onChange({ ...value, whisper_device: e.target.value })
              }
            >
              <option value="cpu">CPU · INT8</option>
              <option value="cuda">NVIDIA CUDA</option>
            </select>
            <small>
              {ui("Whisper 使用 CT2；AMD／Intel GPU 加速請選 Haas")}
            </small>
          </label>
        ))}
      {mode !== "transcribe" &&
        select("llm_device", "llama", "翻譯與人物整理裝置（llama.cpp）")}
      {error && <p className="error">{error}</p>}
    </fieldset>
  );
}
