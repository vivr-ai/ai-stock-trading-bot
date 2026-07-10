import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";

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

export async function GET(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(req.url);
  const search = searchParams.get("search")?.trim() || "";
  const type = searchParams.get("type") || "all"; // all | buy | sell | hold
  const limit = Math.min(Number(searchParams.get("limit")) || 150, 500);

  const where: string[] = [];
  const params: unknown[] = [];

  if (search) {
    params.push(`%${search}%`);
    const idx = params.length;
    where.push(`(symbol ILIKE $${idx} OR reason ILIKE $${idx} OR rationale ILIKE $${idx})`);
  }
  if (type === "buy") {
    where.push("decision = 'buy'");
  } else if (type === "sell") {
    where.push("decision = 'sell'");
  } else if (type === "hold") {
    where.push("decision NOT IN ('buy', 'sell')");
  }

  const whereClause = where.length ? `WHERE ${where.join(" AND ")}` : "";
  params.push(limit);

  try {
    const [decisions, heartbeat] = await Promise.all([
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
