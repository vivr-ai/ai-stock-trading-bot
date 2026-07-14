import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import {
  type ClosedTradeRow, type EquityPoint, computeTradeMetrics,
  collapseToLastPerDay, pctReturns, maxDrawdownPct, sharpeRatio,
} from "@/lib/strategyAnalytics";
import { runPatternDiscovery } from "@/lib/patternDiscovery";
import { computeStrategyHealth } from "@/lib/strategyHealth";
import { generateMonthlyReport, modelIdFor, type MonthlyReportModel } from "@/lib/monthlyReport";

export const dynamic = "force-dynamic";

const TRADE_COLUMNS = `symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                       entry_time, ts, buy_reason, sector, confidence_score,
                       confidence_label, market_regime, strategy_version`;

// Two ways in: a logged-in dashboard session (the "Generate Report Now"
// button), or the bot's scheduled monthly job authenticating with a shared
// secret (see main.py's run_monthly_report / bot/config.py's DashboardConfig).
// The bot has no NextAuth session to present - it's a backend service, not
// a browser - so this is a minimal internal-service auth path, checked
// against DASHBOARD_INTERNAL_API_KEY rather than the human login flow.
async function isAuthorized(req: Request): Promise<boolean> {
  const session = await getServerSession(authOptions);
  if (session) return true;

  const internalKey = process.env.DASHBOARD_INTERNAL_API_KEY;
  if (!internalKey) return false; // no internal auth configured - session-only
  const provided = req.headers.get("x-internal-api-key");
  return provided === internalKey;
}

export async function GET(req: Request) {
  if (!(await isAuthorized(req))) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const reports = await query(
      `SELECT id, generated_at, period_start, period_end, model_used, total_trades,
              strategy_health_score, overall_performance, lessons_learned, emerging_patterns,
              potential_optimizations, market_observations, recommended_improvements,
              telegram_summary, sent_via_telegram
       FROM monthly_research_reports ORDER BY generated_at DESC LIMIT 24`
    );
    return NextResponse.json({ reports });
  } catch (err) {
    console.error("GET /api/monthly-report failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}

export async function POST(req: Request) {
  if (!(await isAuthorized(req))) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const body = await req.json().catch(() => ({}));
    const model: MonthlyReportModel = body?.model === "sonnet" ? "sonnet" : "haiku";
    const periodDays = Number.isFinite(body?.periodDays) && body.periodDays > 0 ? body.periodDays : 30;

    const now = new Date();
    const periodStart = new Date(now.getTime() - periodDays * 86_400_000);

    const [allTrades, periodTrades, snapshots, activeVersion, pendingCount] = await Promise.all([
      query<ClosedTradeRow>(`SELECT ${TRADE_COLUMNS} FROM closed_trades ORDER BY ts ASC LIMIT 20000`),
      query<ClosedTradeRow>(
        `SELECT ${TRADE_COLUMNS} FROM closed_trades WHERE ts >= $1::timestamptz ORDER BY ts ASC`,
        [periodStart.toISOString()]
      ),
      query<EquityPoint>("SELECT ts, portfolio_value FROM portfolio_snapshots ORDER BY ts ASC LIMIT 5000"),
      queryOne<{ version: string }>(
        "SELECT version FROM strategy_versions WHERE is_active = true ORDER BY deployed_at DESC LIMIT 1"
      ),
      queryOne<{ count: string }>(
        "SELECT count(*) FROM strategy_recommendations WHERE status = 'pending'"
      ),
    ]);

    const daily = collapseToLastPerDay(snapshots);
    const returns = pctReturns(daily);
    const equity = {
      maxDrawdownPct: maxDrawdownPct(daily.map((d) => d.value)),
      sharpeRatio: sharpeRatio(returns),
      dailyReturns: returns,
    };

    const findings = runPatternDiscovery(allTrades);
    const qualifyingFindings = findings.filter((f) => f.meetsMinSample);
    const strategyHealth = computeStrategyHealth(allTrades, periodStart.toISOString(), equity);

    const draft = await generateMonthlyReport(
      {
        periodStart: periodStart.toISOString(),
        periodEnd: now.toISOString(),
        totalTradesAllTime: allTrades.length,
        totalTradesThisPeriod: periodTrades.length,
        activeStrategyVersion: activeVersion?.version ?? "v1",
        periodPerformance: computeTradeMetrics(periodTrades, periodDays),
        allTimePerformance: computeTradeMetrics(allTrades, null),
        strategyHealth,
        qualifyingFindings,
        pendingRecommendationsCount: Number(pendingCount?.count ?? 0),
      },
      model
    );

    const row = await queryOne<{ id: number }>(
      `INSERT INTO monthly_research_reports
         (period_start, period_end, model_used, total_trades, strategy_health_score,
          overall_performance, lessons_learned, emerging_patterns, potential_optimizations,
          market_observations, recommended_improvements, telegram_summary)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
       RETURNING id`,
      [
        periodStart.toISOString(), now.toISOString(), modelIdFor(model), periodTrades.length,
        strategyHealth.overallScore, JSON.stringify(computeTradeMetrics(periodTrades, periodDays)),
        draft.lessonsLearned, draft.emergingPatterns, draft.potentialOptimizations,
        draft.marketObservations, draft.recommendedImprovements, draft.telegramSummary,
      ]
    );

    return NextResponse.json({
      ok: true,
      id: row?.id,
      report: draft,
      telegramSummary: draft.telegramSummary,
      modelUsed: modelIdFor(model),
    });
  } catch (err) {
    console.error("POST /api/monthly-report failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
