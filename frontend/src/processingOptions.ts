import type { ManagedModel } from "./ModelManager";
import type { AsrFilterOptions } from "./AsrFilterSettings";
import type { ExecutionOptions } from "./ExecutionSettings";

export type ProcessingMode = "run" | "transcribe" | "translate";
export function missingProcessingModels(
  catalog: ManagedModel[],
  mode: string,
  asr: string,
  translation: string,
  execution: ExecutionOptions,
  filter: AsrFilterOptions,
): ManagedModel[] {
  const required = new Set<string>();
  if (mode !== "translate") {
    required.add(asr);
    if (filter.enabled || execution.vad_engine === "firered")
      required.add("firered-vad");
    if (execution.vad_engine === "silero") required.add("silero-vad");
  }
  if (mode !== "transcribe") required.add(translation);
  return catalog.filter((model) => required.has(model.id) && !model.ready);
}
