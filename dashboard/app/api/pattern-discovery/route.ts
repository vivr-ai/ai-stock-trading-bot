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
//
// Inserted one row at a time (rather than one bulk multi-row INSERT) so
// each finding's generated id can be read back via RETURNING without
// relying on Postgres preserving VALUES-list order - findings counts are
// small (a handful per run), so the extra round-trips are not a concern.
// The returned, id-bearing findings are what maybeCreateRecommendationsFromFindings
// below links each auto-drafted recommendation back to.
async function persistFindings(findings: Finding[]): Promise<Array<Finding & { id: number }>> {
  const withIds: Array<Finding & { id: number }> = [];
  for (const f of findings) {
    const rows = await query<{ id: number }>(
      `INSERT INTO pattern_discovery_findings
         (category, title, description, sample_size, baseline_sample_size,
          statistical_method, p_value, effect_size, meets_min_sample, is_significant)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
       RETURNING id`,
      [
        f.category,
        f.title,
        f.description,
        f.sampleSize,
        f.baselineSampleSize,
        f.statisticalMethod,
        f.pValue,
        f.effectSize,
        f.meetsMinSample,
        f.isSignificant,
      ]
    );
    withIds.push({ ...f, id: rows[0].id });
  }
  return withIds;
}

// The Recommendations <-> Pattern Discovery bridge: a finding strong enough
// to act on (significant, past its category's minimum sample, and at least
// "medium" confidence - i.e. at least 2x the minimum sample) gets drafted
// into strategy_recommendations automatically, as a 'pending' row a human
// still has to review and approve or reject - this never changes what the
// bot does by itself (see that table's header comment). Deduped by
// category: once ANY recommendation has ever been auto-drafted from a given
// FindingCategory, later runs skip it, so re-loading this page repeatedly
// doesn't spam duplicate drafts for the same recurring finding. Each draft
// carries source_category and pattern_discovery_finding_id so the
// Recommendations card can link back to the exact finding it came from -
// essential once there's more than one discovery/recommendation at once.
async function maybeCreateRecommendationsFromFindings(
  findings: Array<Finding & { id: number }>
): Promise<void> {
  const qualifying = findings.filter(
    (f) =>
      f.isSignificant &&
      f.meetsMinSample &&
      (f.confidenceLevel === "medium" || f.confidenceLevel === "high")
  );
  for (const f of qualifying) {
    try {
      const existing = await query<{ id: number }>(
        `SELECT id FROM strategy_recommendations
         WHERE source = 'pattern_discovery' AND source_category = $1 LIMIT 1`,
        [f.category]
      );
      if (existing.length > 0) continue;

      const confidenceText = `${f.confidenceLevel} (n=${f.sampleSize}${
        f.pValue != null ? `, p=${f.pValue.toFixed(3)}` : ""
      })`;
      await query(
        `INSERT INTO strategy_recommendations
           (source, title, observation, evidence, statistical_confidence,
            recommendation, priority, status, source_category, pattern_discovery_finding_id)
         VALUES ('pattern_discovery', $1, $2, $3, $4, $5, $6, 'pending', $7, $8)`,
        [
          f.title,
          f.description,
          f.statisticalMethod,
          confidenceText,
          "Drafted automatically from this Pattern Discovery finding - review the " +
            "evidence in Pattern Discovery before approving or rejecting.",
          f.confidenceLevel === "high" ? "high" : "medium",
          f.category,
          f.id,
        ]
      );
    } catch (err) {
      console.error(
        `Could not auto-draft a recommendation for category ${f.category} (non-fatal)`,
        err
      );
    }
  }
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
    // depend on the write succeeding. The auto-draft bridge only runs when
    // persistence succeeded (it needs each finding's generated id).
    if (findings.length > 0) {
      try {
        const withIds = await persistFindings(findings);
        try {
          await maybeCreateRecommendationsFromFindings(withIds);
        } catch (bridgeErr) {
          console.error("Could not auto-draft recommendations from findings (non-fatal)", bridgeErr);
        }
      } catch (persistErr) {
        console.error("Could not persist pattern_discovery_findings (non-fatal)", persistErr);
      }
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
