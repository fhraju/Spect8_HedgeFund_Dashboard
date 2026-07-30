"use client";

export default function DashboardError({
  reset,
}: {
  reset: () => void;
}) {
  return (
    <main className="loading-shell">
      <section className="error-panel">
        <p className="section-kicker">Data unavailable</p>
        <h1>Dashboard projection could not be loaded</h1>
        <p>
          No signal was created. Check the protected backend connection and try
          again.
        </p>
        <button type="button" onClick={reset}>Retry</button>
      </section>
    </main>
  );
}
