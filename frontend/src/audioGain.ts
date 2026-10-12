// Native media volume is limited to 1. Create a Web Audio route only when boost is requested.
export class MediaVolume {
  private context: AudioContext | null = null;
  private source: MediaElementAudioSourceNode | null = null;
  private gain: GainNode | null = null;

  constructor(private media: HTMLMediaElement, private createContext = () => new AudioContext()) {}

  setLevel(level: number) {
    const value = Number.isFinite(level) ? Math.max(0, Math.min(2, level)) : 1;
    if (value > 1 && !this.context) {
      const context = this.createContext();
      try {
        const gain = context.createGain();
        gain.gain.value = value;
        gain.connect(context.destination);
        const source = context.createMediaElementSource(this.media);
        source.connect(gain);
        this.context = context;
        this.source = source;
        this.gain = gain;
      } catch (error) {
        void context.close().catch(() => {});
        throw error;
      }
    }
    if (this.context && this.gain) {
      // Keep native volume at unity; otherwise lowering it would attenuate twice.
      this.media.volume = 1;
      this.gain.gain.setTargetAtTime(value, this.context.currentTime, 0.015);
    } else {
      this.media.volume = value;
    }
  }

  get state() {return this.context?.state ?? "native";}

  async resume() {
    if (this.context?.state === "suspended") await this.context.resume();
  }

  dispose() {
    this.source?.disconnect();
    this.gain?.disconnect();
    if (this.context) void this.context.close().catch(() => {});
    this.source = null;
    this.gain = null;
    this.context = null;
  }
}
