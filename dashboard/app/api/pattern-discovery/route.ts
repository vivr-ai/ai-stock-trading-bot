import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query } from "@/lib/db";
import type { ClosedTradeRow } from "@/lib/strategyAnalytics";
import { runPatternDiscovery, type Finding } from "@/lib/patternDiscovery";

export const dynamic = "force-dynamic";

const TRADE_COLUMNS = `symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                       entry_time, ts, buy_reason, sector, confidence_score,
                       confidence_label, market_regime, strategy_version`;

// Pattern Discovery recomputes fresh on every hit from ALL-TIME closed
// trades (never a recent window) - this is the direct implementation of
// "do not overfit to recent trades". Findings are persisted to
// pattern_discovery_findings purely as a history/audit trail; the numbers
// shown are always the latest computation, not a stale cached row.
async function persistFindings(findings: Finding[]) {
  if (findings.length === 0) return;
  const values: unknown[] = [];
  const rows: string[] = [];
  findings.forEach((f, i) => {
    const base = i * 10;
    rows.push(
      `($${base + 1}, $${base + 2}, $${base + 3}, $${base + 4}, $${base + 5}, $${base + 6}, $${base + 7}, $${base + 8}, $${base + 9}, $${base + 10})`
    );
    values.push(
      f.category,
      f.title,
      f.description,
      f.sampleSize,
      f.baselineSampleSize,
      f.statisticalMethod,
      f.pValue,
      f.effectSize,
      f.meetsMinSample,
      f.isSignificant
    );
  });
  await query(
    `INSERT INTO pattern_discovery_findings
       (category, title, description, sample_size, baseline_sample_size,
        statistical_method, p_value, effect_size, meets_min_sample, is_significant)
     VALUES ${rows.join(", ")}`,
    values
  );
}

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const trades = await query<ClosedTradeRow>(
      `SELECT ${TRADE_COLUMNS} FROM closed_trades ORDER BY ts ASC LIMIT 20000`
    );

    const findings = runPatternDiscovery(trades);

    // Best-effort persistence for an audit trail - if it fails, we still
    // return the live-computed findings, since the analysis itself doesn't
    // depend on the write succeeding.
    try {
      await persistFindings(findings);
    } catch (persistErr) {
      console.error("Could not persist pattern_discovery_findings (non-fatal)", persistErr);
    }

    return NextResponse.json({
      totalTradesAnalysed: trades.length,
      generatedAt: new Date().toISOString(),
      findings,
    });
  } catch (err) {
    console.error("GET /api/pattern-discovery failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
