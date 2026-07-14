import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import type { ClosedTradeRow } from "@/lib/strategyAnalytics";
import { runBacktest, parseConfigChangeSpec } from "@/lib/backtest";

export const dynamic = "force-dynamic";

const TRADE_COLUMNS = `symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                       entry_time, ts, buy_reason, sector, confidence_score,
                       confidence_label, market_regime, strategy_version`;

// POST /api/backtest: replay an approved recommendation's proposed config
// change against all-time closed trades, before it's deployed as a new
// strategy version. Only runs for recommendations already approved on the
// Recommendations page - backtesting a still-pending or rejected
// recommendation isn't part of the approval gate this exists to support.
export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const body = await req.json().catch(() => ({}));
    const recommendationId = Number(body?.recommendationId);
    if (!recommendationId) {
      return NextResponse.json({ error: "recommendationId is required" }, { status: 400 });
    }

    const rec = await queryOne<{
      id: number;
      status: string;
      proposed_config_changes: Record<string, unknown> | null;
    }>(
      "SELECT id, status, proposed_config_changes FROM strategy_recommendations WHERE id = $1",
      [recommendationId]
    );
    if (!rec) {
      return NextResponse.json({ error: "Recommendation not found" }, { status: 404 });
    }
    if (rec.status !== "approved") {
      return NextResponse.json(
        { error: "Only approved recommendations can be backtested" },
        { status: 400 }
      );
    }

    const change = parseConfigChangeSpec(rec.proposed_config_changes);
    if (!change) {
      return NextResponse.json(
        { error: "This recommendation has no backtestable config change attached to it" },
        { status: 400 }
      );
    }

    const trades = await query<ClosedTradeRow>(
      `SELECT ${TRADE_COLUMNS} FROM closed_trades ORDER BY ts ASC LIMIT 20000`
    );

    const result = runBacktest(trades, change);

    await query(
      "UPDATE strategy_recommendations SET backtest_result = $1 WHERE id = $2",
      [JSON.stringify(result), recommendationId]
    );

    return NextResponse.json({ ok: true, result });
  } catch (err) {
    console.error("POST /api/backtest failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
