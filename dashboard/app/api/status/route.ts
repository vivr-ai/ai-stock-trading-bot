/**
 * SUPERSEDED - the Home page now calls /api/home instead (see
 * app/api/home/route.ts), which also fixes a bug this route still has:
 * portfolioValue/cash go null whenever the latest heartbeat happens to be
 * a market-closed cycle (which skips the Alpaca account call by design -
 * see bot/trading/strategy.py's run_cycle early return). /api/home falls
 * back to the latest portfolio_snapshots row instead. Left in place only
 * because nothing currently calls it, not because it's still correct -
 * safe to delete next time you're editing locally.
 */
import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";

export const dynamic = "force-dynamic";

// A heartbeat older than this is treated as "Stopped" - the scheduler runs
// every 30 min during market hours, so a 45-min gap means something's wrong
// (or the market's closed and the bot correctly went quiet).
const STALE_HEARTBEAT_MINUTES = 45;

type Heartbeat = {
  ts: string;
  status: string;
  scheduler_status: string;
  market_open: boolean | null;
  dry_run: boolean | null;
  portfolio_value: number | null;
  cash: number | null;
  equity: number | null;
  buying_power: number | null;
  open_positions: number | null;
  message: string | null;
};

type LastTrade = {
  ts: string;
  action: string;
  symbol: string;
  qty: number | null;
  price: number | null;
  dry_run: boolean;
  status: string | null;
};

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const [heartbeat, firstSnapshot, openPositions, lastTrade, todaysClosedPnl] =
      await Promise.all([
        queryOne<Heartbeat>("SELECT * FROM heartbeats ORDER BY ts DESC LIMIT 1"),
        queryOne<{ portfolio_value: number | null }>(
          "SELECT portfolio_value FROM portfolio_snapshots ORDER BY ts ASC LIMIT 1"
        ),
        query<{ count: string; total_unrealized: string | null }>(
          "SELECT count(*), sum(unrealized_pl) as total_unrealized FROM open_positions"
        ),
        queryOne<LastTrade>(
          `SELECT ts, action, symbol, qty, price, dry_run, status
           FROM trades ORDER BY ts DESC LIMIT 1`
        ),
        queryOne<{ total: string | null }>(
          `SELECT sum(pnl) as total FROM closed_trades
           WHERE ts >= date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc'`
        ),
      ]);

    const now = Date.now();
    const heartbeatAgeMinutes = heartbeat
      ? (now - new Date(heartbeat.ts).getTime()) / 60000
      : null;
    const botStatus =
      heartbeat && heartbeatAgeMinutes !== null && heartbeatAgeMinutes < STALE_HEARTBEAT_MINUTES
        ? heartbeat.status === "error"
          ? "error"
          : "running"
        : "stopped";

    const portfolioValue = heartbeat?.portfolio_value ?? null;
    const baselineValue = firstSnapshot?.portfolio_value ?? null;
    const totalReturnPct =
      portfolioValue != null && baselineValue
        ? ((portfolioValue - baselineValue) / baselineValue) * 100
        : null;

    const realizedToday = todaysClosedPnl?.total ? Number(todaysClosedPnl.total) : 0;
    const unrealizedNow = openPositions[0]?.total_unrealized
      ? Number(openPositions[0].total_unrealized)
      : 0;
    const todaysPl = realizedToday + unrealizedNow;

    return NextResponse.json({
      botStatus,
      lastHeartbeat: heartbeat?.ts ?? null,
      schedulerStatus: heartbeat?.scheduler_status ?? null,
      marketOpen: heartbeat?.market_open ?? null,
      dryRun: heartbeat?.dry_run ?? null,
      portfolioValue,
      cash: heartbeat?.cash ?? null,
      equity: heartbeat?.equity ?? null,
      buyingPower: heartbeat?.buying_power ?? null,
      totalReturnPct,
      openPositionsCount: openPositions[0] ? Number(openPositions[0].count) : 0,
      todaysPl,
      realizedToday,
      unrealizedNow,
      lastTrade: lastTrade
        ? {
            ts: lastTrade.ts,
            action: lastTrade.action,
            symbol: lastTrade.symbol,
            qty: lastTrade.qty,
            price: lastTrade.price,
            dryRun: lastTrade.dry_run,
            status: lastTrade.status,
          }
        : null,
      hasAnyData: Boolean(heartbeat),
    });
  } catch (err) {
    console.error("GET /api/status failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
