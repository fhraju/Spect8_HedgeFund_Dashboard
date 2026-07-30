import { Dashboard } from "@/components/dashboard";
import { getDashboardSnapshot } from "@/lib/backend";
import { requireDashboardSession } from "@/lib/server-auth";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  await requireDashboardSession();
  const snapshot = await getDashboardSnapshot();
  return <Dashboard snapshot={snapshot} />;
}
