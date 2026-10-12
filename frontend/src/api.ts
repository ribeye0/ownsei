export type Project = {
  id: string;
  title: string;
  path: string;
  tracks: number;
  created_at?: number;
  trashed_at?: number | null;
};

export async function api<T>(path: string, method = "GET", body?: unknown,
  options: {signal?: AbortSignal; timeoutMs?: number} = {}): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  options.signal?.addEventListener("abort", cancel, {once:true});
  if (options.signal?.aborted) cancel();
  let timedOut = false;
  // The timer covers response headers AND the JSON body. A stalled response
  // must not hold all workspace controls indefinitely.
  const timer = setTimeout(() => {timedOut = true; controller.abort();}, options.timeoutMs ?? 30000);
  let rejectAbort: (reason: unknown) => void;
  const aborted = new Promise<never>((_, reject) => {rejectAbort = reject;});
  const onAbort = () => rejectAbort(new DOMException("Aborted", "AbortError"));
  controller.signal.addEventListener("abort", onAbort, {once:true});
  if (controller.signal.aborted) onAbort();
  try {
    const request = async () => {
      const response = await fetch("/api" + path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        if (controller.signal.aborted) throw new DOMException("Aborted", "AbortError");
        throw Error(typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail));
      }
      return await response.json();
    };
    // A hard deadline also releases the UI if the browser fails to settle an
    // aborted response body. The pending request's eventual rejection is observed.
    return await Promise.race([request(), aborted]);
  } catch (e) {
    if (timedOut) throw Error(`請求逾時：${method} ${path}。介面已解除等待，可重新載入；操作是否已完成請以目前結果為準。`);
    throw e;
  } finally {
    clearTimeout(timer);
    controller.signal.removeEventListener("abort", onAbort);
    options.signal?.removeEventListener("abort", cancel);
  }
}
