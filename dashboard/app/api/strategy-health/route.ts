import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import {
  type ClosedTradeRow, type EquityPoint,
  collapseToLastPerDay, pctReturns, maxDrawdownPct, sharpeRatio,
} from "@/lib/strategyAnalytics";
import { computeStrategyHealth } from "@/lib/strategyHealth";

export const dynamic = "force-dynamic";

const TRADE_COLUMNS = `symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                       entry_time, ts, buy_reason, sector, confidence_score,
                       confidence_label, market_regime, strategy_version`;

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const [trades, snapshots, activeVersion, history] = await Promise.all([
      query<ClosedTradeRow>(`SELECT ${TRADE_COLUMNS} FROM closed_trades ORDER BY ts ASC LIMIT 20000`),
      query<EquityPoint>("SELECT ts, portfolio_value FROM portfolio_snapshots ORDER BY ts ASC LIMIT 5000"),
      queryOne<{ version: string }>(
        "SELECT version FROM strategy_versions WHERE is_active = true ORDER BY deployed_at DESC LIMIT 1"
      ),
      query<{ computed_at: string; overall_score: number | null; confidence_level: string }>(
        `SELECT computed_at, overall_score, confidence_level FROM strategy_health_scores
         ORDER BY computed_at ASC LIMIT 500`
      ),
    ]);

    const daily = collapseToLastPerDay(snapshots);
    const returns = pctReturns(daily);
    const equity = {
      maxDrawdownPct: maxDrawdownPct(daily.map((d) => d.value)),
      sharpeRatio: sharpeRatio(returns),
      dailyReturns: returns,
    };

    const recentCutoffIso = new Date(Date.now() - 30 * 86_400_000).toISOString();
    const health = computeStrategyHealth(trades, recentCutoffIso, equity);

    // Best-effort persistence for the trend chart - the "current" numbers
    // returned to the page are always the fresh computation above, whether
    // or not this write succeeds.
    try {
      await query(
        `INSERT INTO strategy_health_scores
           (overall_score, confidence_level, total_trades, strategy_version, components)
         VALUES ($1, $2, $3, $4, $5)`,
        [
          health.overallScore, health.confidenceLevel, health.totalTrades,
          activeVersion?.version ?? null, JSON.stringify(health.components),
        ]
      );
    } catch (persistErr) {
      console.error("Could not persist strategy_health_scores (non-fatal)", persistErr);
    }

    return NextResponse.json({
      current: health,
      activeStrategyVersion: activeVersion?.version ?? "v1",
      history,
    });
  } catch (err) {
    console.error("GET /api/strategy-health failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
