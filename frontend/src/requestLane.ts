/** Replace obsolete reads, and never apply a response from an older selection. */
export class RequestLane {
  private controller: AbortController | null = null;
  constructor(private onPending: (pending: boolean) => void = () => {}) {}

  cancel() {
    const previous = this.controller;
    this.controller = null;
    previous?.abort();
    this.onPending(false);
  }

  async run<T>(load: (signal: AbortSignal) => Promise<T>, apply: (value: T) => void) {
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    this.onPending(true);
    try {
      const result = await load(controller.signal);
      if (this.controller === controller) apply(result);
    } catch (e) {
      if (this.controller === controller) throw e;
    } finally {
      if (this.controller === controller) {
        this.controller = null;
        controller.abort(); // Also release sibling reads if Promise.all rejected.
        this.onPending(false);
      }
    }
  }
}
