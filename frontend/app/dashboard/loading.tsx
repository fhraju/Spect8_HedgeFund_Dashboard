export default function DashboardLoading() {
  return (
    <main className="loading-shell" aria-busy="true">
      <section>
        <p className="section-kicker">Protected manager workspace</p>
        <h1>Loading EUR/USD scanner state</h1>
        <div className="loading-grid">
          {Array.from({ length: 8 }).map((_, index) => (
            <span key={index} />
          ))}
        </div>
      </section>
    </main>
  );
}
