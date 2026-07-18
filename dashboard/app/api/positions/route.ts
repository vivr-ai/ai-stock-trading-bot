import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";

export const dynamic = "force-dynamic";

type OpenPosition = {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  allocation_pct: number | null;
  ai_confidence: number | null;
  entry_reason: string | null;
  entry_time: string | null;
  updated_at: string;
};

type EntryOrderLevels = {
  symbol: string;
  stop_price: number | null;
  take_profit: number | null;
};

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const [positions, heartbeat] = await Promise.all([
      query<OpenPosition>("SELECT * FROM open_positions ORDER BY market_value DESC NULLS LAST"),
      queryOne<{ ts: string }>("SELECT ts FROM heartbeats ORDER BY ts DESC LIMIT 1"),
    ]);

    // Stop-loss/take-profit aren't columns on open_positions (that table
    // only tracks the current book), but every buy order - real or dry-run
    // - has them recorded on its trades row (see bot/trading/strategy.py's
    // _do_buy -> record_trade). Look up each open symbol's most recent buy
    // to attach the bracket levels the bot set when it opened the position.
    // If a symbol was bought more than once (rare - re-entry after cooldown
    // resets these), this shows the levels from the LATEST buy, matching
    // the qty/avg-entry the open_positions row itself reflects.
    let levelsBySymbol = new Map<string, EntryOrderLevels>();
    if (positions.length > 0) {
      const symbols = positions.map((p) => p.symbol);
      const levels = await query<EntryOrderLevels>(
        `SELECT DISTINCT ON (symbol) symbol, stop_price, take_profit
         FROM trades
         WHERE action = 'buy' AND symbol = ANY($1::text[])
         ORDER BY symbol, ts DESC`,
        [symbols]
      );
      levelsBySymbol = new Map(levels.map((l) => [l.symbol, l]));
    }

    const positionsWithLevels = positions.map((p) => ({
      ...p,
      stop_loss_price: levelsBySymbol.get(p.symbol)?.stop_price ?? null,
      take_profit_price: levelsBySymbol.get(p.symbol)?.take_profit ?? null,
    }));

    return NextResponse.json({
      positions: positionsWithLevels,
      hasEverRun: Boolean(heartbeat),
      updatedAt: positions[0]?.updated_at ?? null,
    });
  } catch (err) {
    console.error("GET /api/positions failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
