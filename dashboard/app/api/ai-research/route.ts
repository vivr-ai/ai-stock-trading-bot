import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import {
  type ClosedTradeRow, type EquityPoint, computeTradeMetrics,
  collapseToLastPerDay, pctReturns, maxDrawdownPct, sharpeRatio,
} from "@/lib/strategyAnalytics";
import { runPatternDiscovery } from "@/lib/patternDiscovery";
import { generateResearchReport, modelIdFor, type ResearchModel } from "@/lib/aiResearch";

export const dynamic = "force-dynamic";

const TRADE_COLUMNS = `symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                       entry_time, ts, buy_reason, sector, confidence_score,
                       confidence_label, market_regime, strategy_version`;

async function allTimeEquityMetrics() {
  const snapshots = await query<EquityPoint>(
    "SELECT ts, portfolio_value FROM portfolio_snapshots ORDER BY ts ASC LIMIT 5000"
  );
  const daily = collapseToLastPerDay(snapshots);
  const returns = pctReturns(daily);
  const values = daily.map((d) => d.value);
  return { maxDrawdownPct: maxDrawdownPct(values), sharpeRatio: sharpeRatio(returns) };
}

// POST /api/ai-research: on-demand trigger for the AI Research Assistant.
// Phase 7 (Monthly Research Report automation) will call this same function
// on a schedule with model="haiku"; this route is the on-demand path where
// the user can request model="sonnet" for deeper analysis before making a
// strategy decision.
export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const body = await req.json().catch(() => ({}));
    const model: ResearchModel = body?.model === "sonnet" ? "sonnet" : "haiku";

    const [trades, activeVersion] = await Promise.all([
      query<ClosedTradeRow>(`SELECT ${TRADE_COLUMNS} FROM closed_trades ORDER BY ts ASC LIMIT 20000`),
      queryOne<{ version: string }>(
        "SELECT version FROM strategy_versions WHERE is_active = true ORDER BY deployed_at DESC LIMIT 1"
      ),
    ]);

    if (trades.length === 0) {
      return NextResponse.json({
        ok: true,
        created: 0,
        recommendations: [],
        note: "No closed trades yet - nothing to analyse.",
      });
    }

    const findings = runPatternDiscovery(trades);
    const qualifyingFindings = findings.filter((f) => f.meetsMinSample);
    const equity = await allTimeEquityMetrics();
    const performance = { ...computeTradeMetrics(trades, null), ...equity };

    const drafts = await generateResearchReport(
      {
        totalTradesAnalysed: trades.length,
        activeStrategyVersion: activeVersion?.version ?? "v1",
        performance,
        qualifyingFindings,
      },
      model
    );

    if (drafts.length === 0) {
      return NextResponse.json({
        ok: true,
        created: 0,
        recommendations: [],
        modelUsed: modelIdFor(model),
        note:
          qualifyingFindings.length === 0
            ? "No findings currently meet the minimum sample size - the AI Research Assistant was not run to avoid generating ungrounded recommendations."
            : "The AI Research Assistant reviewed the qualifying findings and did not identify anything actionable to recommend right now.",
      });
    }

    const inserted: { id: number }[] = [];
    for (const d of drafts) {
      const row = await queryOne<{ id: number }>(
        `INSERT INTO strategy_recommendations
           (source, title, observation, evidence, statistical_confidence, estimated_impact,
            risks, recommendation, priority)
         VALUES ('ai_research_assistant', $1, $2, $3, $4, $5, $6, $7, $8)
         RETURNING id`,
        [
          d.title, d.observation, d.evidence, d.statisticalConfidence,
          d.estimatedImpact, d.risks, d.recommendation, d.priority,
        ]
      );
      if (row) inserted.push(row);
    }

    return NextResponse.json({
      ok: true,
      created: inserted.length,
      modelUsed: modelIdFor(model),
      qualifyingFindingsCount: qualifyingFindings.length,
    });
  } catch (err) {
    console.error("POST /api/ai-research failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
