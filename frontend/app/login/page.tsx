import { redirect } from "next/navigation";

import { hasDashboardSession } from "@/lib/server-auth";

type LoginPageProps = {
  searchParams: Promise<{ error?: string; logged_out?: string }>;
};

export default async function LoginPage({ searchParams }: LoginPageProps) {
  if (await hasDashboardSession()) {
    redirect("/dashboard");
  }
  const params = await searchParams;
  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="login-brand">
          <span className="brand-symbol">S8</span>
          <div>
            <strong>Spect8</strong>
            <small>Strategy Intelligence</small>
          </div>
        </div>
        <p className="section-kicker">Protected client workspace</p>
        <h1>Market Scanner</h1>
        <p className="login-copy">
          Sign in to the read-only EUR/USD dashboard.
        </p>
        {params.error ? (
          <p className="auth-message error" role="alert">
            Authentication failed.
          </p>
        ) : null}
        {params.logged_out ? (
          <p className="auth-message success">Session closed securely.</p>
        ) : null}
        <form action="/api/auth/login" method="post">
          <label htmlFor="password">Client password</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
          />
          <button type="submit">Enter dashboard</button>
        </form>
        <p className="synthetic-login-note">
          Phase 3A · EUR/USD strategy monitoring · no trading
        </p>
      </section>
    </main>
  );
}
