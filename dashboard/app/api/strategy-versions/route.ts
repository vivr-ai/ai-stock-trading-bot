import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import { type ClosedTradeRow, computeTradeMetrics } from "@/lib/strategyAnalytics";

export const dynamic = "force-dynamic";

const TRADE_COLUMNS = `symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                       entry_time, ts, buy_reason, sector, confidence_score,
                       confidence_label, market_regime, strategy_version`;

type VersionRow = {
  version: string;
  deployed_at: string;
  description: string | null;
  config_snapshot: Record<string, unknown> | null;
  is_active: boolean;
  created_from_recommendation_id: number | null;
};

// GET: every version, each with its own performance metrics computed from
// closed_trades tagged with that version. Trades are attributed to a
// version by strategy_version (set at ENTRY time - see bot/trading/
// strategy.py) so a trade opened under v1 and closed after v2 deployed is
// still correctly counted against v1, not v2.
export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const versions = await query<VersionRow>(
      "SELECT version, deployed_at, description, config_snapshot, is_active, created_from_recommendation_id FROM strategy_versions ORDER BY deployed_at ASC"
    );

    const allTrades = await query<ClosedTradeRow>(
      `SELECT ${TRADE_COLUMNS} FROM closed_trades ORDER BY ts ASC LIMIT 20000`
    );

    const result = versions.map((v) => {
      const tradesForVersion = allTrades.filter(
        (t) => (t.strategy_version ?? "v1") === v.version
      );
      const metrics = computeTradeMetrics(tradesForVersion, null);
      const totalReturn = tradesForVersion.reduce((a, t) => a + Number(t.pnl), 0);
      return {
        ...v,
        metrics: { ...metrics, totalReturn },
      };
    });

    return NextResponse.json({ versions: result });
  } catch (err) {
    console.error("GET /api/strategy-versions failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}

// POST: deploy a new strategy version. This is the ONLY action that changes
// what the bot tags new trades with (bot/strategy_version.py reads whichever
// row has is_active=true) - always a deliberate human action taken here on
// the dashboard, never automatic. Deactivates whatever was previously active.
export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const body = await req.json();
    const { version, description, configSnapshot, fromRecommendationId } = body as {
      version?: string;
      description?: string;
      configSnapshot?: Record<string, unknown>;
      fromRecommendationId?: number;
    };

    if (!version || typeof version !== "string") {
      return NextResponse.json({ error: "version is required" }, { status: 400 });
    }

    const existing = await queryOne<{ version: string }>(
      "SELECT version FROM strategy_versions WHERE version = $1",
      [version]
    );
    if (existing) {
      return NextResponse.json({ error: `Version '${version}' already exists` }, { status: 409 });
    }

    await query("UPDATE strategy_versions SET is_active = false WHERE is_active = true");
    await query(
      `INSERT INTO strategy_versions
        (version, description, config_snapshot, is_active, created_from_recommendation_id)
       VALUES ($1, $2, $3, true, $4)`,
      [version, description ?? null, configSnapshot ? JSON.stringify(configSnapshot) : null,
       fromRecommendationId ?? null]
    );

    if (fromRecommendationId) {
      await query(
        "UPDATE strategy_recommendations SET deployed_as_version = $1 WHERE id = $2",
        [version, fromRecommendationId]
      );
    }

    return NextResponse.json({ ok: true, version });
  } catch (err) {
    console.error("POST /api/strategy-versions failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
