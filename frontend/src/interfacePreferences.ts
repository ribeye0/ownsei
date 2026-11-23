import { api } from "./api";

const keys = {
  "ownsei:locale": "locale",
  "ownsei:theme": "theme",
  "ownsei:export-preferences:v1": "export",
  "ownsei:vulkan:execution:v1": "execution",
  "ownsei:vad-options:v1": "vad",
  "ownsei:asr-filter:v1": "asr_filter",
  "ownsei:follow-playback": "follow_playback",
  "onsei:playback-rate": "playback_rate",
} as const;
type Key = keyof typeof keys;
type Settings = Record<string, unknown>;
let current: Settings = {};
let pending = Promise.resolve();
const writes = new Map<string, Promise<void>>();
let changes: BroadcastChannel | undefined;

function decode(key: Key, value: string): unknown {
  return key === "ownsei:locale" || key === "ownsei:theme" ? value : JSON.parse(value);
}

export async function loadInterfacePreferences() {
  const legacy: Settings = {};
  for (const key of Object.keys(keys) as Key[]) {
    try {
      const value = localStorage.getItem(key);
      if (value !== null) legacy[keys[key]] = decode(key, value);
    } catch { /* Unavailable storage or a malformed legacy preference. */ }
  }
  await pending;
  current = await api<Settings>("/preferences/interface", "POST", { values: legacy, import_missing: true });
  if (!changes && typeof window !== "undefined" && "BroadcastChannel" in window) {
    changes = new BroadcastChannel("ownsei:interface");
    changes.onmessage = ({ data }) => {
      if (data?.field === "locale" && !writes.has("locale")) {
        current.locale = data.value;
        window.dispatchEvent(new Event("ownsei:preferences"));
      }
    };
  }
}

export function readPreference(key: Key): string | null {
  const value = current[keys[key]];
  return value == null ? null : typeof value === "string" ? value : JSON.stringify(value);
}

export function savePreference(key: Key, value: string): Promise<void> {
  const field = keys[key];
  if (readPreference(key) === value) return writes.get(field) ?? Promise.resolve();
  const decoded = decode(key, value);
  current[field] = decoded;
  const write = pending.then(async () => {
    await api("/preferences/interface", "POST", { values: { [field]: decoded } }, {keepalive: true});
    if (field === "locale") changes?.postMessage({ field, value: decoded });
  });
  writes.set(field, write);
  pending = write.catch(error => {
    if (writes.get(field) === write) delete current[field];
    window.dispatchEvent(new CustomEvent("ownsei:settings-error", { detail: String(error) }));
  }).finally(() => {
    if (writes.get(field) === write) writes.delete(field);
  });
  return write;
}

// React change handlers update immediately. Export's explicit save awaits the write.
export function writePreference(key: Key, value: string): void {
  void savePreference(key, value).catch(() => {});
}
