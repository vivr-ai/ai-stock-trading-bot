import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";

export const dynamic = "force-dynamic";

type Snapshot = { ts: string | Date; portfolio_value: number | null };
type ClosedTradeAgg = {
  total: string;
  wins: string;
  avg_gain: string | null;
  avg_loss: string | null;
};

// node-postgres returns TIMESTAMPTZ columns as native Date objects (not
// strings) when read directly from a query result - it only becomes a
// string after JSON.stringify serializes the API response. These helpers
// run server-side on the raw query rows, so they need to handle a Date.
function toIso(value: string | Date): string {
  return value instanceof Date ? value.toISOString() : value;
}
function dayKey(iso: string | Date) {
  return toIso(iso).slice(0, 10); // YYYY-MM-DD (UTC)
}
function monthKey(iso: string | Date) {
  return toIso(iso).slice(0, 7); // YYYY-MM
}

/** Collapse a time series to one point per key (the LAST point for that key),
 * then return [{ key, value }] in chronological order. */
function collapseToLast(points: Snapshot[], keyFn: (iso: string | Date) => string) {
  const map = new Map<string, number>();
  for (const p of points) {
    if (p.portfolio_value == null) continue;
    map.set(keyFn(p.ts), p.portfolio_value);
  }
  return Array.from(map.entries()).map(([key, value]) => ({ key, value }));
}

function pctReturns(series: { key: string; value: number }[]) {
  const out: { key: string; pct: number }[] = [];
  for (let i = 1; i < series.length; i++) {
    const prev = series[i - 1].value;
    const cur = series[i].value;
    if (prev > 0) out.push({ key: series[i].key, pct: ((cur - prev) / prev) * 100 });
  }
  return out;
}

function maxDrawdownPct(values: number[]): number | null {
  if (values.length === 0) return null;
  let peak = values[0];
  let worst = 0;
  for (const v of values) {
    if (v > peak) peak = v;
    if (peak > 0) worst = Math.max(worst, ((peak - v) / peak) * 100);
  }
  return worst;
}

function sharpeRatio(dailyPctReturns: number[]): number | null {
  if (dailyPctReturns.length < 2) return null;
  const decimals = dailyPctReturns.map((p) => p / 100);
  const mean = decimals.reduce((a, b) => a + b, 0) / decimals.length;
  const variance =
    decimals.reduce((a, b) => a + (b - mean) ** 2, 0) / (decimals.length - 1);
  const stddev = Math.sqrt(variance);
  if (stddev === 0) return null;
  return (mean / stddev) * Math.sqrt(252);
}

export async function GET(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // `from` is an optional ISO timestamp (see the page's PeriodSelector,
  // which computes it client-side from the chosen preset - 1W/1M/3M/6M/
  // YTD/1Y/FY/All - in the viewer's own local calendar, since "financial
  // year" and "year to date" are personal/tax concepts, not the bot's).
  // Omitted or invalid = "All", the original unfiltered behavior.
  const { searchParams } = new URL(req.url);
  const fromRaw = searchParams.get("from");
  const from = fromRaw && !Number.isNaN(Date.parse(fromRaw)) ? fromRaw : null;

  try {
    const [snapshotsInWindow, baselineRows, tradeAgg, heartbeat] = await Promise.all([
      query<Snapshot>(
        from
          ? "SELECT ts, portfolio_value FROM portfolio_snapshots WHERE ts >= $1 ORDER BY ts ASC LIMIT 5000"
          : "SELECT ts, portfolio_value FROM portfolio_snapshots ORDER BY ts ASC LIMIT 5000",
        from ? [from] : []
      ),
      // One snapshot from strictly before `from`'s own calendar day (not
      // just before `from` itself, which is almost never midnight - a
      // same-day baseline would collapse into the same dayKey bucket as
      // the window's first day and silently swallow its return bar
      // instead of fixing it). Used ONLY as the reference point for the
      // first in-window day/month's % return (a "1 Week" view still
      // needs ~7 daily bars, not 6, the same way a stock chart's day-1
      // change is relative to the prior close) - never shown on the
      // equity curve itself, which starts exactly at `from`, and never
      // counted in maxDrawdown/Sharpe (those reflect the window only).
      from
        ? query<Snapshot>(
            `SELECT ts, portfolio_value FROM portfolio_snapshots
             WHERE ts < date_trunc('day', $1::timestamptz)
             ORDER BY ts DESC LIMIT 1`,
            [from]
          )
        : Promise.resolve<Snapshot[]>([]),
      queryOne<ClosedTradeAgg>(
        `SELECT count(*) as total,
                count(*) FILTER (WHERE pnl > 0) as wins,
                avg(pnl) FILTER (WHERE pnl > 0) as avg_gain,
                avg(pnl) FILTER (WHERE pnl < 0) as avg_loss
         FROM closed_trades
         ${from ? "WHERE ts >= $1" : ""}`,
        from ? [from] : []
      ),
      queryOne<{ ts: string }>("SELECT ts FROM heartbeats ORDER BY ts DESC LIMIT 1"),
    ]);

    const snapshotsForReturns = [...baselineRows, ...snapshotsInWindow];
    const dailySeries = collapseToLast(snapshotsForReturns, dayKey);
    const monthlySeries = collapseToLast(snapshotsForReturns, monthKey);
    const dailyReturns = pctReturns(dailySeries);
    const monthlyReturns = pctReturns(monthlySeries);

    const equityValues = snapshotsInWindow
      .map((s) => s.portfolio_value)
      .filter((v): v is number => v != null);

    const total = tradeAgg ? Number(tradeAgg.total) : 0;
    const wins = tradeAgg ? Number(tradeAgg.wins) : 0;

    return NextResponse.json({
      hasAnyData: Boolean(heartbeat),
      equityCurve: snapshotsInWindow
        .filter((s) => s.portfolio_value != null)
        .map((s) => ({ ts: s.ts, value: s.portfolio_value })),
      dailyReturns: dailyReturns.map((d) => ({ label: d.key, pct: d.pct })),
      monthlyReturns: monthlyReturns.map((m) => ({ label: m.key, pct: m.pct })),
      winRatePct: total > 0 ? (wins / total) * 100 : null,
      avgGain: tradeAgg?.avg_gain != null ? Number(tradeAgg.avg_gain) : null,
      avgLoss: tradeAgg?.avg_loss != null ? Number(tradeAgg.avg_loss) : null,
      maxDrawdownPct: maxDrawdownPct(equityValues),
      sharpeRatio: sharpeRatio(dailyReturns.map((d) => d.pct)),
      totalTrades: total,
    });
  } catch (err) {
    console.error("GET /api/performance failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
