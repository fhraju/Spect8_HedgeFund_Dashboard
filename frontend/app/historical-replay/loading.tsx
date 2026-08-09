import { LogoutButton } from "@/components/logout-button";

export default function HistoricalReplayLoading() {
  return (
    <main className="loading-shell">
      <LogoutButton className="standalone-logout" />
      <section>
        <span className="section-kicker">REPLAY — NOT LIVE</span>
        <h1>Loading historical replay</h1>
        <p className="login-copy">Reading isolated replay status and persisted evaluations.</p>
        <div className="loading-grid"><span /><span /><span /><span /></div>
      </section>
    </main>
  );
}
