import { afterEach, expect, it, vi } from "vitest";
import { SignalClock } from "@/lib/signal-clock";

afterEach(() => vi.useRealTimers());

it("uses server time across device clock skew, refreshes and wall-clock corrections", () => {
  vi.useFakeTimers();
  let elapsed = 100;
  const clock = new SignalClock(() => elapsed);
  const server = Date.parse("2026-09-12T02:00:00Z");
  for (const skew of [-650, 650, -3600000, 3600000]) {
    vi.setSystemTime(server + skew);
    expect(clock.receive(new Date(server).toISOString())).toBe(server);
    elapsed += 750;
    vi.setSystemTime(server - skew);
    expect(clock.now()).toBe(server + 750);
  }
  elapsed += 31000;
  expect(clock.now()).toBe(server + 31750);
});

it("rejects missing/malformed response clocks without replacing the valid anchor", () => {
  let elapsed = 0;
  const clock = new SignalClock(() => elapsed);
  expect(clock.now()).toBeNaN();
  const server = Date.parse("2026-09-12T02:00:00Z");
  clock.receive(new Date(server).toISOString());
  for (const value of [null, "invalid"]) expect(() => clock.receive(value)).toThrow();
  elapsed = 1000;
  expect(clock.now()).toBe(server + 1000);
});
