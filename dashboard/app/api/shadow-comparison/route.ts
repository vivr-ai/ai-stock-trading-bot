import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import { type ClosedTradeRow, computeTradeMetrics, type PerformanceMetrics } from "@/lib/strategyAnalytics";
import { strategyConfig } from "@/lib/strategyConfig";

export const dynamic = "force-dynamic";

// Strategy v2, Path B (RSI-2 mean-reversion) runs in shadow mode by default
// (STRATEGY_REVERSION_LIVE=false): it never places a real order, it only
// logs what it would have bought ('reversion_shadow_buy') and, once the
// bot's own exit rule fires against that hypothetical position, what it
// would have closed at ('reversion_shadow_exit' - see
// SentimentStrategy._maybe_resolve_shadow_position in
// bot/trading/strategy.py). Neither of those carry qty/notional (there's no
// real position to size), so every shadow figure here is a PERCENTAGE
// return, not a dollar one - that's the only fair unit to compare against a
// hypothetical trade of arbitrary size.
//
// This route reconstructs shadow round-trips by pairing each symbol's
// 'reversion_shadow_buy' rows with its next 'reversion_shadow_exit', and
// separately computes live performance (Path A momentum, and Path B once/if
// STRATEGY_REVERSION_LIVE is ever flipped on) over the same time window, so
// the two are comparable.

type ShadowDecisionRow = {
  id: number;
  ts: string;
  symbol: string;
  decision: "reversion_shadow_buy" | "reversion_shadow_exit";
  reason: string | null;
  price: number | null;
  extra: Record<string, unknown> | null;
};

export type ShadowEpisode = {
  symbol: string;
  status: "open" | "closed";
  entryTs: string;
  entryPrice: number;
  entryReason: string | null;
  exitTs: string | null;
  exitPrice: number | null;
  exitReason: string | null;
  // For a closed episode this is the realized return. For a still-open one,
  // it's unrealized, marked against the price on the most recent re-firing
  // of the same signal (lastMarkTs) - not a live quote, so it can lag by up
  // to one cycle (~30 min) behind the real market.
  pnlPct: number | null;
  heldDays: number | null;
  lastMarkTs: string | null;
};

export type ShadowStats = {
  closedCount: number;
  openCount: number;
  winRatePct: number | null;
  avgPnlPct: number | null;
  totalPnlPct: number | null; // simple sum across closed episodes, not compounded
  bestPnlPct: number | null;
  worstPnlPct: number | null;
  avgHeldDays: number | null;
};

type LiveStats = PerformanceMetrics & { totalPnl: number | null };

function buildEpisodes(rows: ShadowDecisionRow[]): ShadowEpisode[] {
  const bySymbol = new Map<string, ShadowDecisionRow[]>();
  for (const r of rows) {
    if (!bySymbol.has(r.symbol)) bySymbol.set(r.symbol, []);
    bySymbol.get(r.symbol)!.push(r);
  }

  const episodes: ShadowEpisode[] = [];
  const now = Date.now();

  for (const [symbol, symRows] of bySymbol.entries()) {
    const sorted = [...symRows].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());
    let open: ShadowEpisode | null = null;

    for (const row of sorted) {
      if (row.decision === "reversion_shadow_buy") {
        if (open == null) {
          open = {
            symbol,
            status: "open",
            entryTs: row.ts,
            entryPrice: row.price ?? 0,
            entryReason: row.reason,
            exitTs: null,
            exitPrice: null,
            exitReason: null,
            pnlPct: null,
            heldDays: null,
            lastMarkTs: row.ts,
          };
        } else if (row.price != null) {
          // Same signal re-firing while still open (this happened routinely
          // before shadow-position tracking was added - the bot re-logged
          // "would buy" every cycle a dip stayed oversold; treated as an
          // updated mark on the SAME episode, not a new one, so trade counts
          // aren't inflated by re-firings of an unresolved signal).
          open.lastMarkTs = row.ts;
        }
        continue;
      }
      // reversion_shadow_exit
      if (open == null) continue; // exit with no tracked open episode - ignore, can't attribute
      const extra = row.extra ?? {};
      const pnlPct =
        typeof extra.pnl_pct === "number"
          ? extra.pnl_pct
          : open.entryPrice > 0 && row.price != null
          ? ((row.price - open.entryPrice) / open.entryPrice) * 100
          : null;
      const heldDays =
        typeof extra.held_days === "number"
          ? extra.held_days
          : (new Date(row.ts).getTime() - new Date(open.entryTs).getTime()) / 86_400_000;
      episodes.push({
        ...open,
        status: "closed",
        exitTs: row.ts,
        exitPrice: row.price,
        exitReason: row.reason,
        pnlPct,
        heldDays,
      });
      open = null;
    }

    if (open != null) {
      // Still open as of the most recent data we have. pnlPct is left null
      // here and backfilled by the caller against the last price actually
      // seen for this symbol (a re-firing of the signal, or entry itself) -
      // see the lastMarkPrice pass in GET() below.
      episodes.push({
        ...open,
        heldDays: (now - new Date(open.entryTs).getTime()) / 86_400_000,
      });
    }
  }

  return episodes.sort((a, b) => new Date(b.entryTs).getTime() - new Date(a.entryTs).getTime());
}

function computeShadowStats(episodes: ShadowEpisode[]): ShadowStats {
  const closed = episodes.filter((e) => e.status === "closed" && e.pnlPct != null);
  const open = episodes.filter((e) => e.status === "open");
  const wins = closed.filter((e) => (e.pnlPct as number) > 0);
  const pnlPcts = closed.map((e) => e.pnlPct as number);
  const heldDaysList = closed.map((e) => e.heldDays).filter((d): d is number => d != null);
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  return {
    closedCount: closed.length,
    openCount: open.length,
    winRatePct: closed.length > 0 ? (wins.length / closed.length) * 100 : null,
    avgPnlPct: pnlPcts.length > 0 ? sum(pnlPcts) / pnlPcts.length : null,
    totalPnlPct: pnlPcts.length > 0 ? sum(pnlPcts) : null,
    bestPnlPct: pnlPcts.length > 0 ? Math.max(...pnlPcts) : null,
    worstPnlPct: pnlPcts.length > 0 ? Math.min(...pnlPcts) : null,
    avgHeldDays: heldDaysList.length > 0 ? sum(heldDaysList) / heldDaysList.length : null,
  };
}

const TRADE_COLUMNS = `symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                       entry_time, ts, buy_reason, sector, confidence_score,
                       confidence_label, market_regime, strategy_version`;

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const shadowRows = await query<ShadowDecisionRow>(
      `SELECT id, ts, symbol, decision, reason, price, extra
       FROM decisions
       WHERE decision IN ('reversion_shadow_buy', 'reversion_shadow_exit')
       ORDER BY ts ASC
       LIMIT 20000`
    );

    const episodesRaw = buildEpisodes(shadowRows);
    // Backfill unrealized P/L for still-open episodes against the last mark
    // price we actually saw (a re-firing of the signal, or entry itself).
    const lastMarkPrice = new Map<string, number>();
    for (const r of shadowRows) {
      if (r.decision === "reversion_shadow_buy" && r.price != null) {
        lastMarkPrice.set(r.symbol, r.price);
      }
    }
    const episodes = episodesRaw.map((e) => {
      if (e.status !== "open") return e;
      const mark = lastMarkPrice.get(e.symbol) ?? e.entryPrice;
      const pnlPct = e.entryPrice > 0 ? ((mark - e.entryPrice) / e.entryPrice) * 100 : null;
      return { ...e, pnlPct };
    });

    const shadowStats = computeShadowStats(episodes);
    const sinceTs = shadowRows.length > 0 ? shadowRows[0].ts : null;

    // Live comparison over the same window: trades tagged by entry_path
    // (Strategy v2 - see bot/trading/strategy.py). Pre-v2 trades have
    // entry_path=NULL and are treated as sentiment_momentum, since that was
    // the bot's only path before this. Attribution is by JOINing trades
    // (which has entry_path) to closed_trades (which doesn't) on
    // symbol + entry_time, matching the same pattern app/api/decisions
    // uses for "outcome" annotation.
    const [buyTrades, closedTrades] = await Promise.all([
      query<{ symbol: string; ts: string; entry_path: string | null }>(
        `SELECT symbol, ts, entry_path FROM trades
         WHERE action = 'buy' ${sinceTs ? "AND ts >= $1" : ""}
         ORDER BY ts ASC LIMIT 20000`,
        sinceTs ? [sinceTs] : []
      ),
      query<ClosedTradeRow>(
        `SELECT ${TRADE_COLUMNS} FROM closed_trades
         ${sinceTs ? "WHERE ts >= $1" : ""}
         ORDER BY ts ASC LIMIT 20000`,
        sinceTs ? [sinceTs] : []
      ),
    ]);

    function entryPathFor(c: ClosedTradeRow): string {
      if (!c.entry_time) return "sentiment_momentum";
      const entryMs = new Date(c.entry_time).getTime();
      const match = buyTrades.find(
        (b) => b.symbol === c.symbol && Math.abs(new Date(b.ts).getTime() - entryMs) < 5 * 60_000
      );
      return match?.entry_path ?? "sentiment_momentum";
    }

    const momentumClosed: ClosedTradeRow[] = [];
    const reversionLiveClosed: ClosedTradeRow[] = [];
    for (const c of closedTrades) {
      (entryPathFor(c) === "mean_reversion" ? reversionLiveClosed : momentumClosed).push(c);
    }

    function withTotalPnl(rows: ClosedTradeRow[]): LiveStats {
      const metrics = computeTradeMetrics(rows);
      const totalPnl = rows.length > 0 ? rows.reduce((a, r) => a + Number(r.pnl), 0) : null;
      return { ...metrics, totalPnl };
    }

    return NextResponse.json({
      hasEverRun: Boolean(await queryOne("SELECT 1 FROM heartbeats LIMIT 1")),
      sinceTs,
      episodes,
      shadowStats,
      liveMomentumStats: withTotalPnl(momentumClosed),
      liveReversionStats: withTotalPnl(reversionLiveClosed),
      reversionLiveEnabled: reversionLiveClosed.length > 0,
      reversionMaxHoldDays: strategyConfig.reversionMaxHoldDays,
    });
  } catch (err) {
    console.error("GET /api/shadow-comparison failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
