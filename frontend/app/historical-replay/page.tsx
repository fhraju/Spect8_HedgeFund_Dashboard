import { HistoricalReplayDashboard } from "@/components/historical-replay-dashboard";
import {
  getHistoricalReplayEvaluation,
  getHistoricalReplayEvaluations,
  getHistoricalReplayRuns,
  getHistoricalReplaySummary,
} from "@/lib/backend";
import { requireDashboardSession } from "@/lib/server-auth";

export const dynamic = "force-dynamic";

type SearchValue = string | string[] | undefined;

function first(value: SearchValue): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function HistoricalReplayPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, SearchValue>>;
}) {
  await requireDashboardSession();
  const query = await searchParams;
  const requestedRun = first(query.run);
  const filters = {
    run: requestedRun,
    timeframe: ["H1", "H4"].includes(first(query.timeframe) ?? "")
      ? first(query.timeframe)
      : undefined,
    outcome: ["SIGNAL", "NO_SIGNAL"].includes(first(query.outcome) ?? "")
      ? first(query.outcome)
      : undefined,
    filter_outcome: ["PASS", "FAIL"].includes(
      first(query.filter_outcome) ?? "",
    )
      ? first(query.filter_outcome)
      : undefined,
    reason_code: /^[A-Z0-9_]{1,80}$/.test(first(query.reason_code) ?? "")
      ? first(query.reason_code)
      : undefined,
    page: first(query.page) ?? "1",
    evaluation: first(query.evaluation),
  };
  try {
    const runsResponse = await getHistoricalReplayRuns();
    const runs = runsResponse.data.items;
    const run = runs.find((item) => item.run_id === requestedRun) ?? runs[0];
    if (!run) {
      return (
        <HistoricalReplayDashboard
          runs={[]}
          summary={null}
          evaluations={null}
          detail={null}
          filters={filters}
          createError={first(query.create_error) === "1"}
        />
      );
    }
    filters.run = run.run_id;
    const page = Math.max(1, Number.parseInt(filters.page, 10) || 1);
    const [summaryResponse, evaluationsResponse] = await Promise.all([
      getHistoricalReplaySummary(run.run_id),
      getHistoricalReplayEvaluations(run.run_id, {
        page,
        timeframe: filters.timeframe,
        outcome: filters.outcome,
        filter_outcome: filters.filter_outcome,
        reason_code: filters.reason_code,
      }),
    ]);
    const evaluationId = Number.parseInt(filters.evaluation ?? "", 10);
    const detail = Number.isFinite(evaluationId)
      ? (await getHistoricalReplayEvaluation(run.run_id, evaluationId)).data
      : null;
    return (
      <HistoricalReplayDashboard
        runs={runs}
        summary={summaryResponse.data}
        evaluations={evaluationsResponse.data}
        detail={detail}
        filters={filters}
        createError={first(query.create_error) === "1"}
      />
    );
  } catch {
    return (
      <HistoricalReplayDashboard
        runs={[]}
        summary={null}
        evaluations={null}
        detail={null}
        filters={filters}
        createError
      />
    );
  }
}
