export type TransportState = "paused" | "playing" | "seeking" | "buffering";

// Native seeking keeps the decoder playing; intent also survives an explicit recovery reload.
export class AudioTransport {
  private wanted = false;
  private pending: number | null = null;
  private ignoredPauses = 0;
  private revision = 0;
  private disposed = false;
  private recovered = false;
  private nudged = false;
  private lastTime: number;
  private lastProgress: number;
  private timer: ReturnType<typeof setInterval>;
  private listeners: [string, EventListener][] = [];

  constructor(private media: HTMLMediaElement, private resumeAudio: () => Promise<void>,
    private status: (state: TransportState) => void, private error: (message: string) => void,
    private clock = () => performance.now(), private trace: (message: string) => void = () => {}) {
    this.lastTime = media.currentTime;
    this.lastProgress = clock();
    this.listen("play", () => {if (!media.paused) {
      if (!this.wanted) {this.nudged = false; this.recovered = false;}
      this.wanted = true; media.preload = "auto"; this.progress(); this.syncState(); this.resume();
    }});
    this.listen("playing", () => {if (!media.paused) this.syncState();});
    this.listen("pause", () => {
      if (this.ignoredPauses) {this.ignoredPauses--; return;}
      this.pause();
    });
    this.listen("seeking", () => {if (this.pending === null) this.progress(); this.status("seeking");});
    this.listen("seeked", () => this.finishSeek());
    this.listen("canplay", () => {
      if (this.wanted) {if (media.paused) this.start(); else this.syncState();}
    });
    this.listen("waiting", () => {if (this.wanted) this.status(media.seeking ? "seeking" : "buffering");});
    this.listen("stalled", () => {if (this.wanted) this.status("buffering");});
    this.listen("loadedmetadata", () => {
      if (this.pending !== null) media.currentTime = this.pending;
      if (this.wanted) this.start();
    });
    this.listen("timeupdate", () => {
      if (!media.seeking && Math.abs(media.currentTime - this.lastTime) > 0.02) {
        this.progress();
        this.pending = null;
        this.syncState();
      }
    });
    for (const event of ["loadstart", "loadeddata", "suspend", "abort", "emptied"]) this.listen(event, () => {});
    this.listen("ended", () => this.pause());
    this.listen("error", () => this.pause());
    this.trace("source:attached");
    this.timer = setInterval(() => this.checkProgress(), 250);
  }

  private listen(name: string, handler: () => void) {
    const listener = () => {if (name !== "timeupdate") this.trace(`event:${name}`); handler();};
    this.media.addEventListener(name, listener);
    this.listeners.push([name, listener]);
  }

  private progress() {
    this.lastTime = this.media.currentTime;
    this.lastProgress = this.clock();
  }

  private resume() {
    const revision = this.revision;
    void this.resumeAudio().catch(e => {
      if (!this.disposed && revision === this.revision) this.fail(`音量增益恢復失敗：${String(e)}`);
    });
  }

  private syncState() {
    this.status(this.media.seeking ? "seeking" : !this.media.paused && this.media.readyState >= 3 ? "playing" : this.wanted ? "buffering" : "paused");
  }

  private start() {
    if (!this.wanted || this.disposed) return;
    const revision = this.revision;
    // Promote metadata-only loading before requesting playback, without resetting the decoder.
    this.media.preload = "auto";
    this.trace("play:request");
    this.resume();
    this.syncState();
    void this.media.play().then(() => {
      if (!this.disposed && revision === this.revision && this.wanted) {this.trace("play:resolved"); this.syncState();}
    }).catch(e => {
      if (this.disposed || revision !== this.revision || e?.name === "AbortError") return;
      this.fail(`無法播放音檔：${String(e)}`);
    });
  }

  play() {
    if (this.disposed) return;
    this.nudged = false;
    this.revision++;
    this.wanted = true;
    this.recovered = false;
    this.progress();
    if (this.media.ended) this.seek(0);
    else this.start();
  }

  pause() {
    if (this.disposed) return;
    this.revision++;
    this.wanted = false;
    this.pending = null;
    this.status("paused");
    if (!this.media.paused) this.media.pause();
  }

  toggle() {if (this.wanted || !this.media.paused) this.pause(); else this.play();}

  seek(position: number) {
    if (this.disposed) return;
    this.nudged = false;
    this.trace(`seek:target=${position.toFixed(3)}`);
    this.wanted = this.wanted || !this.media.paused;
    this.revision++;
    this.pending = position;
    this.recovered = false;
    this.progress();
    this.status("seeking");
    this.resume();
    this.media.currentTime = position;
    // Do not wait for seeked before play: paused/preload=metadata can defer target data.
    if (this.wanted) this.start();
    if (!this.media.seeking) this.finishSeek();
  }

  private finishSeek() {
    // The native seeking flag is authoritative, including quantized codec timestamps.
    if (this.media.seeking) return;
    this.pending = null;
    if (this.wanted) this.start(); else this.status("paused");
  }

  private checkProgress() {
    if (!this.wanted && this.pending === null) return;
    if (!this.media.paused && !this.media.seeking && Math.abs(this.media.currentTime - this.lastTime) > 0.02) {this.progress(); return;}
    const elapsed = this.clock() - this.lastProgress;
    if (this.wanted && !this.nudged && !this.recovered && !this.media.seeking
      && this.media.readyState >= 1 && elapsed >= 1500) {
      // A same-position native seek can unblock a suspended startup/decoder without skipping audio.
      // Only try once per request, after measured lack of progress; do not reload healthy playback.
      this.nudged = true;
      this.pending ??= this.media.currentTime;
      this.trace(`recovery:seek-current=${this.pending.toFixed(3)}`);
      this.revision++;
      this.media.currentTime = this.pending;
      this.start();
      return;
    }
    if (elapsed < 8000) return;
    if (this.recovered) {this.fail("播放載入逾時；請按「重試播放」。"); return;}
    // One bounded decoder reload, preserving the source, target and playback intent.
    this.trace("recovery:reload");
    this.recovered = true;
    this.pending ??= this.media.currentTime;
    this.revision++;
    if (!this.media.paused) {this.ignoredPauses++; this.media.pause();}
    this.status("buffering");
    this.progress();
    this.media.load();
  }

  retry() {
    if (this.disposed) return;
    this.nudged = false;
    this.wanted = true;
    this.pending ??= this.media.currentTime;
    this.recovered = false;
    this.revision++;
    if (!this.media.paused) {this.ignoredPauses++; this.media.pause();}
    this.progress();
    this.status("buffering");
    this.resume();
    this.media.load();
  }

  private fail(message: string) {
    this.trace(`error:${message}`);
    this.pending = null;
    this.pause();
    this.error(message);
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    clearInterval(this.timer);
    for (const [name, listener] of this.listeners) this.media.removeEventListener(name, listener);
    this.media.pause();
    // Detached audio elements can retain downloads/decoders; pause alone does not abort them.
    this.media.removeAttribute("src");
    this.media.load();
  }
}
