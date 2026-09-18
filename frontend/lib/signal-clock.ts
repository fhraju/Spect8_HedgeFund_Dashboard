/** Server-time anchor advanced by elapsed browser time, independent of device UTC. */
export class SignalClock {
  private anchor: { utc: number; elapsed: number } | null = null;

  constructor(private elapsed: () => number = () => performance.now()) {}

  receive(responseTime: string | null): number {
    const utc = Date.parse(responseTime ?? "");
    if (!Number.isFinite(utc)) throw new Error("Signal response clock unavailable");
    this.anchor = { utc, elapsed: this.elapsed() };
    return utc;
  }

  now(): number {
    if (!this.anchor) return Number.NaN;
    return this.anchor.utc + Math.max(0, this.elapsed() - this.anchor.elapsed);
  }
}
