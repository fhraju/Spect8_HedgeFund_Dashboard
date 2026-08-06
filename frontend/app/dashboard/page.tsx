import { MarketScanner } from "@/components/market-scanner";
import { getScannerSnapshot } from "@/lib/backend";
import { requireDashboardSession } from "@/lib/server-auth";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  await requireDashboardSession();
  const snapshot = await getScannerSnapshot();
  return <MarketScanner snapshot={snapshot} />;
}
