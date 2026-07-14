import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import {
  type ClosedTradeRow,
  type EquityPoint,
  computeTradeMetrics,
  breakdownBy,
  confidenceBucketKey,
  sectorKey,
  symbolKey,
  dayOfWeekKey,
  hourOfDayKey,
  sentimentLabelKey,
  marketRegimeKey,
  collapseToLastPerDay,
  pctReturns,
  maxDrawdownPct,
  sharpeRatio,
} from "@/lib/strategyAnalytics";

export const dynamic = "force-dynamic";

const TRADE_COLUMNS = `symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                       entry_time, ts, buy_reason, sector, confidence_score,
                       confidence_label, market_regime, strategy_version`;

function buildBreakdowns(trades: ClosedTradeRow[]) {
  return {
    byConfidence: breakdownBy(trades, confidenceBucketKey),
    bySector: breakdownBy(trades, sectorKey),
    bySymbol: breakdownBy(trades, symbolKey),
    byDayOfWeek: breakdownBy(trades, dayOfWeekKey),
    byHourOfDay: breakdownBy(trades, hourOfDayKey),
    bySentimentLabel: breakdownBy(trades, sentimentLabelKey),
    byMarketRegime: breakdownBy(trades, marketRegimeKey),
  };
}

async function equityMetrics(sinceIso: string | null) {
  const snapshots = await query<EquityPoint>(
    sinceIso
      ? "SELECT ts, portfolio_value FROM portfolio_snapshots WHERE ts >= $1::timestamptz ORDER BY ts ASC LIMIT 5000"
      : "SELECT ts, portfolio_value FROM portfolio_snapshots ORDER BY ts ASC LIMIT 5000",
    sinceIso ? [sinceIso] : []
  );
  const daily = collapseToLastPerDay(snapshots);
  const returns = pctReturns(daily);
  const values = daily.map((d) => d.value);
  return {
    maxDrawdownPct: maxDrawdownPct(values),
    sharpeRatio: sharpeRatio(returns),
  };
}

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const now = new Date();
    const d30 = new Date(now.getTime() - 30 * 86_400_000).toISOString();
    const d90 = new Date(now.getTime() - 90 * 86_400_000).toISOString();

    const [allTimeTrades, trades30, trades90, activeVersion, latestHeartbeat, earliestTrade] =
      await Promise.all([
        query<ClosedTradeRow>(
          `SELECT ${TRADE_COLUMNS} FROM closed_trades ORDER BY ts ASC LIMIT 20000`
        ),
        query<ClosedTradeRow>(
          `SELECT ${TRADE_COLUMNS} FROM closed_trades WHERE ts >= $1::timestamptz ORDER BY ts ASC`,
          [d30]
        ),
        query<ClosedTradeRow>(
          `SELECT ${TRADE_COLUMNS} FROM closed_trades WHERE ts >= $1::timestamptz ORDER BY ts ASC`,
          [d90]
        ),
        queryOne<{ version: string; deployed_at: string; description: string | null }>(
          "SELECT version, deployed_at, description FROM strategy_versions WHERE is_active = true ORDER BY deployed_at DESC LIMIT 1"
        ),
        queryOne<{ ts: string; market_regime: string | null }>(
          "SELECT ts, market_regime FROM heartbeats ORDER BY ts DESC LIMIT 1"
        ),
        queryOne<{ ts: string }>("SELECT ts FROM closed_trades ORDER BY ts ASC LIMIT 1"),
      ]);

    const hasAnyData = allTimeTrades.length > 0;

    const [allTimeEquity, equity30, equity90] = await Promise.all([
      equityMetrics(null),
      equityMetrics(d30),
      equityMetrics(d90),
    ]);

    const tradesSinceVersion = activeVersion
      ? allTimeTrades.filter((t) => (t.strategy_version ?? "v1") === activeVersion.version)
      : allTimeTrades;

    // Confidence in the analysis itself scales with how much history exists
    // - descriptive, not a formal statistical test (Phase 3 adds that for
    // individual pattern recommendations). This is just "how much can you
    // trust these numbers".
    const totalTrades = allTimeTrades.length;
    const analysisConfidence =
      totalTrades >= 200 ? "high" : totalTrades >= 50 ? "medium" : totalTrades > 0 ? "low" : "none";

    return NextResponse.json({
      hasAnyData,
      executiveSummary: {
        currentStrategyVersion: activeVersion?.version ?? "v1",
        strategyDeployedAt: activeVersion?.deployed_at ?? earliestTrade?.ts ?? null,
        tradesAnalysedSinceVersion: tradesSinceVersion.length,
        analysisConfidence,
        lastAnalysisDate: new Date().toISOString(),
        currentMarketRegime: latestHeartbeat?.market_regime ?? null,
      },
      periods: {
        allTime: {
          ...computeTradeMetrics(allTimeTrades, null),
          ...allTimeEquity,
          breakdowns: buildBreakdowns(allTimeTrades),
        },
        last30Days: {
          ...computeTradeMetrics(trades30, 30),
          ...equity30,
          breakdowns: buildBreakdowns(trades30),
        },
        last90Days: {
          ...computeTradeMetrics(trades90, 90),
          ...equity90,
          breakdowns: buildBreakdowns(trades90),
        },
      },
    });
  } catch (err) {
    console.error("GET /api/strategy-intelligence failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
