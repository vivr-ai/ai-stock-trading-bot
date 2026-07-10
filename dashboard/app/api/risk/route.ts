import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import { sectorOf, SECTOR_LABELS } from "@/lib/sectors";
import { riskConfig } from "@/lib/riskConfig";

export const dynamic = "force-dynamic";

type Heartbeat = {
  ts: string;
  portfolio_value: number | null;
  cash: number | null;
  equity: number | null;
  buying_power: number | null;
  market_open: boolean | null;
};

type OpenPosition = {
  symbol: string;
  qty: number;
  market_value: number | null;
  allocation_pct: number | null;
  current_price: number | null;
  avg_entry_price: number | null;
};

const REASON_TEXT: Record<string, string> = {
  daily_loss_limit: "Daily loss limit reached — only sentiment-driven exits are allowed, no new buys",
  intraday_drop: "Broad market (SPY) is down sharply today — new entries paused",
  regime_data_unavailable: "Market regime data unavailable — new entries paused as a precaution",
  below_market_regime_sma: "Broad market (SPY) is below its long-term average — new entries paused",
};

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const [heartbeat, positions, todayFirstSnapshot, recentBlocks] = await Promise.all([
      queryOne<Heartbeat>(
        "SELECT ts, portfolio_value, cash, equity, buying_power, market_open FROM heartbeats ORDER BY ts DESC LIMIT 1"
      ),
      query<OpenPosition>(
        "SELECT symbol, qty, market_value, allocation_pct, current_price, avg_entry_price FROM open_positions ORDER BY market_value DESC NULLS LAST"
      ),
      queryOne<{ portfolio_value: number | null }>(
        `SELECT portfolio_value FROM portfolio_snapshots
         WHERE ts >= date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc'
         ORDER BY ts ASC LIMIT 1`
      ),
      query<{ reason: string | null; ts: string }>(
        `SELECT DISTINCT ON (reason) reason, ts FROM decisions
         WHERE decision = 'block_new_entries' AND ts >= now() - interval '45 minutes'
         ORDER BY reason, ts DESC`
      ),
    ]);

    const portfolioValue = heartbeat?.portfolio_value ?? null;
    const cash = heartbeat?.cash ?? null;

    const totalMarketValue = positions.reduce((sum, p) => sum + Number(p.market_value ?? 0), 0);
    const totalExposurePct =
      portfolioValue && portfolioValue > 0 ? (totalMarketValue / portfolioValue) * 100 : null;
    const cashPct =
      portfolioValue && portfolioValue > 0 && cash != null ? (cash / portfolioValue) * 100 : null;

    const sectorMap = new Map<string, { marketValue: number; count: number; symbols: string[] }>();
    for (const p of positions) {
      const sector = sectorOf(p.symbol);
      const entry = sectorMap.get(sector) ?? { marketValue: 0, count: 0, symbols: [] };
      entry.marketValue += Number(p.market_value ?? 0);
      entry.count += 1;
      entry.symbols.push(p.symbol);
      sectorMap.set(sector, entry);
    }
    const sectorExposure = Array.from(sectorMap.entries())
      .map(([sector, v]) => ({
        sector,
        label: SECTOR_LABELS[sector] ?? sector,
        count: v.count,
        marketValue: v.marketValue,
        pctOfPortfolio: portfolioValue && portfolioValue > 0 ? (v.marketValue / portfolioValue) * 100 : null,
        symbols: v.symbols,
        atCap: v.count >= riskConfig.maxPositionsPerSector,
      }))
      .sort((a, b) => b.marketValue - a.marketValue);

    const largestPosition = positions.reduce<OpenPosition | null>((max, p) => {
      if (!max) return p;
      return Number(p.allocation_pct ?? 0) > Number(max.allocation_pct ?? 0) ? p : max;
    }, null);

    const startOfDayValue = todayFirstSnapshot?.portfolio_value ?? null;
    const dailyPnlPct =
      startOfDayValue && startOfDayValue > 0 && portfolioValue != null
        ? ((portfolioValue - startOfDayValue) / startOfDayValue) * 100
        : null;
    const dailyLossBreached = dailyPnlPct != null && dailyPnlPct <= -riskConfig.dailyLossLimitPct;

    const alerts: { severity: "critical" | "warning" | "info"; message: string }[] = [];

    if (dailyLossBreached) {
      alerts.push({
        severity: "critical",
        message: `Daily loss limit hit: portfolio is down ${Math.abs(dailyPnlPct!).toFixed(
          1
        )}% today (limit ${riskConfig.dailyLossLimitPct}%). New buys are paused until tomorrow.`,
      });
    }
    for (const block of recentBlocks) {
      if (block.reason && block.reason !== "daily_loss_limit" && REASON_TEXT[block.reason]) {
        alerts.push({ severity: "warning", message: REASON_TEXT[block.reason] });
      }
    }
    for (const s of sectorExposure) {
      if (s.atCap) {
        alerts.push({
          severity: "info",
          message: `${s.label} is at the sector cap (${s.count}/${riskConfig.maxPositionsPerSector} positions) — no new buys in this sector.`,
        });
      }
    }
    if (positions.length >= riskConfig.maxOpenPositions) {
      alerts.push({
        severity: "info",
        message: `At the max open-position count (${positions.length}/${riskConfig.maxOpenPositions}) — a position must close before the bot can open another.`,
      });
    }
    if (largestPosition && Number(largestPosition.allocation_pct ?? 0) > riskConfig.maxPositionPct + 1) {
      alerts.push({
        severity: "warning",
        message: `${largestPosition.symbol} has grown to ${Number(largestPosition.allocation_pct).toFixed(
          1
        )}% of the portfolio, above the ${riskConfig.maxPositionPct}% per-position target (price moves can push a position past its entry-time size).`,
      });
    }
    if (totalExposurePct != null && totalExposurePct > riskConfig.maxTotalExposurePct + 2) {
      alerts.push({
        severity: "warning",
        message: `Total exposure is ${totalExposurePct.toFixed(1)}%, above the ${riskConfig.maxTotalExposurePct}% target.`,
      });
    }

    return NextResponse.json({
      hasEverRun: Boolean(heartbeat),
      portfolioValue,
      cash,
      cashPct,
      totalExposurePct,
      openPositionsCount: positions.length,
      largestPosition: largestPosition
        ? { symbol: largestPosition.symbol, allocationPct: largestPosition.allocation_pct }
        : null,
      dailyPnlPct,
      dailyLossBreached,
      sectorExposure,
      alerts,
      config: riskConfig,
    });
  } catch (err) {
    console.error("GET /api/risk failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
