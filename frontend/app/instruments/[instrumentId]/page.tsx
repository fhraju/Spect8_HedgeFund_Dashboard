import { Dashboard } from "@/components/dashboard";
import { getInstrumentDashboardSnapshot } from "@/lib/backend";
import { requireDashboardSession } from "@/lib/server-auth";

export const dynamic = "force-dynamic";

export default async function InstrumentPage({
  params,
}: {
  params: Promise<{ instrumentId: string }>;
}) {
  await requireDashboardSession();
  const { instrumentId } = await params;
  const snapshot = await getInstrumentDashboardSnapshot(instrumentId);
  return <Dashboard snapshot={snapshot} />;
}
