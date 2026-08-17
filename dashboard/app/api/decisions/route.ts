import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import { strategyConfig } from "@/lib/strategyConfig";

export const dynamic = "force-dynamic";

type DecisionRow = {
  id: number;
  ts: string;
  symbol: string;
  decision: string;
  reason: string | null;
  sentiment_score: number | null;
  sentiment_label: string | null;
  headline_count: number | null;
  rationale: string | null;
  price: number | null;
};

type ClosedTradeLite = { symbol: string; entry_time: string | null; pnl: number };

// Every symbol is evaluated in two steps each cycle: a "scan" row is written
// first with the confidence/headline context, and - only if the score cleared
// the buy threshold - a second row records what happened next (bought, or
// blocked by a gate like SMA/volume/sector cap). Shown separately, the scan
// row looks like an unexplained Hold. This merges each scan row with its
// companion outcome row (same symbol, shortly after) into one row that has
// both the confidence context AND the reason, and synthesizes a reason for
// scan rows that never got a companion (most commonly: score never reached
// the buy threshold, so no buy was even considered).
const OUTCOME_DECISIONS = new Set([
  "buy",
  "sell",
  "buy_skipped",
  "buy_blocked",
  "buy_failed",
  "sell_skipped",
]);
const PAIR_WINDOW_MS = 2 * 60_000;

function tsMs(d: DecisionRow): number {
  return new Date(d.ts).getTime();
}

function fallbackScanReason(d: DecisionRow): string {
  if (d.sentiment_score != null && d.sentiment_score < strategyConfig.buyThreshold) {
    return `Confidence (${d.sentiment_score.toFixed(1)}) is below the buy threshold (${strategyConfig.buyThreshold.toFixed(
      1
    )}) - no buy was considered this cycle.`;
  }
  if (d.headline_count != null && d.headline_count < strategyConfig.minHeadlines) {
    return `Only ${d.headline_count} headline(s) found, below the minimum of ${strategyConfig.minHeadlines} needed to act.`;
  }
  return "Confidence cleared the buy threshold, but no buy was evaluated this cycle - most likely because the bot was already holding this position, or new entries were paused.";
}

function mergeScanPairs(rows: DecisionRow[]): DecisionRow[] {
  const asc = [...rows].sort((a, b) => tsMs(a) - tsMs(b) || a.id - b.id);
  const consumed = new Set<number>();
  const merged: DecisionRow[] = [];

  for (let i = 0; i < asc.length; i++) {
    const row = asc[i];
    if (consumed.has(row.id)) continue;
    if (row.decision !== "scan") {
      merged.push(row);
      continue;
    }

    let pairIdx = -1;
    for (let j = i + 1; j < asc.length; j++) {
      const cand = asc[j];
      if (tsMs(cand) - tsMs(row) > PAIR_WINDOW_MS) break;
      if (cand.symbol === row.symbol && OUTCOME_DECISIONS.has(cand.decision)) {
        pairIdx = j;
        break;
      }
    }

    if (pairIdx === -1) {
      merged.push({ ...row, reason: fallbackScanReason(row) });
      continue;
    }

    const outcome = asc[pairIdx];
    consumed.add(outcome.id);
    merged.push({
      ...row,
      id: outcome.decision === "buy" || outcome.decision === "sell" ? outcome.id : row.id,
      decision: outcome.decision,
      reason: outcome.reason,
      price: outcome.price ?? row.price,
    });
  }

  return merged.sort((a, b) => tsMs(b) - tsMs(a) || b.id - a.id);
}

export async function GET(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(req.url);
  const search = searchParams.get("search")?.trim() || "";
  const type = searchParams.get("type") || "all"; // all | buy | sell | hold
  const limit = Math.min(Number(searchParams.get("limit")) || 150, 500);
  // Fetch a wider raw window than `limit` (unfiltered by type) so scan/outcome
  // pairs aren't split across the type filter boundary - see mergeScanPairs.
  // The type filter is applied in-memory after merging.
  const rawLimit = Math.min(limit * 3, 1500);

  const where: string[] = [];
  const params: unknown[] = [];

  if (search) {
    params.push(`%${search}%`);
    const idx = params.length;
    where.push(`(symbol ILIKE $${idx} OR reason ILIKE $${idx} OR rationale ILIKE $${idx})`);
  }

  const whereClause = where.length ? `WHERE ${where.join(" AND ")}` : "";
  params.push(rawLimit);

  try {
    const [rawDecisions, heartbeat] = await Promise.all([
      query<DecisionRow>(
        `SELECT id, ts, symbol, decision, reason, sentiment_score, sentiment_label,
                headline_count, rationale, price
         FROM decisions
         ${whereClause}
         ORDER BY ts DESC
         LIMIT $${params.length}`,
        params
      ),
      queryOne<{ ts: string }>("SELECT ts FROM heartbeats ORDER BY ts DESC LIMIT 1"),
    ]);

    const merged = mergeScanPairs(rawDecisions);
    const typeFiltered =
      type === "buy"
        ? merged.filter((d) => d.decision === "buy")
        : type === "sell"
        ? merged.filter((d) => d.decision === "sell")
        : type === "hold"
        ? merged.filter((d) => d.decision !== "buy" && d.decision !== "sell")
        : merged;
    const decisions = typeFiltered.slice(0, limit);

    // Annotate buy/sell rows with a "final outcome" - closed (with P/L) or
    // still open - by cross-referencing open_positions and recent closed_trades.
    const tradeSymbols = Array.from(
      new Set(decisions.filter((d) => d.decision === "buy" || d.decision === "sell").map((d) => d.symbol))
    );

    let openSymbols = new Set<string>();
    let closedTrades: ClosedTradeLite[] = [];
    if (tradeSymbols.length > 0) {
      const [openRows, closedRows] = await Promise.all([
        query<{ symbol: string }>(
          `SELECT symbol FROM open_positions WHERE symbol = ANY($1)`,
          [tradeSymbols]
        ),
        query<ClosedTradeLite>(
          `SELECT symbol, entry_time, pnl FROM closed_trades WHERE symbol = ANY($1) ORDER BY ts DESC LIMIT 500`,
          [tradeSymbols]
        ),
      ]);
      openSymbols = new Set(openRows.map((r) => r.symbol));
      closedTrades = closedRows;
    }

    const annotated = decisions.map((d) => {
      if (d.decision !== "buy" && d.decision !== "sell") {
        return { ...d, outcome: null };
      }
      if (d.decision === "buy") {
        const match = closedTrades.find(
          (c) =>
            c.symbol === d.symbol &&
            c.entry_time &&
            Math.abs(new Date(c.entry_time).getTime() - new Date(d.ts).getTime()) < 5 * 60_000
        );
        if (match) {
          return { ...d, outcome: match.pnl >= 0 ? `Closed +$${match.pnl.toFixed(2)}` : `Closed -$${Math.abs(match.pnl).toFixed(2)}` };
        }
        if (openSymbols.has(d.symbol)) {
          return { ...d, outcome: "Still open" };
        }
        return { ...d, outcome: null };
      }
      // sell: outcome is really "sold" - the closed_trades row for this exit has the P/L
      const match = closedTrades.find(
        (c) => c.symbol === d.symbol
      );
      return { ...d, outcome: match ? (match.pnl >= 0 ? `Closed +$${match.pnl.toFixed(2)}` : `Closed -$${Math.abs(match.pnl).toFixed(2)}`) : null };
    });

    return NextResponse.json({ decisions: annotated, hasEverRun: Boolean(heartbeat) });
  } catch (err) {
    console.error("GET /api/decisions failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
