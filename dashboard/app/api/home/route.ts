import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import { riskConfig } from "@/lib/riskConfig";
import {
  collapseToLastPerDay,
  maxDrawdownPct,
  type ClosedTradeRow,
} from "@/lib/strategyAnalytics";

export const dynamic = "force-dynamic";

// Mirrors /api/status and /api/system-health's threshold - the scheduler
// runs every 30 min during market hours, so a longer gap means the bot has
// actually stopped (or the market's closed, handled separately).
const STALE_HEARTBEAT_MINUTES = 45;

// ---- Market-hours heuristics ---------------------------------------------
// The dashboard has no direct Alpaca/exchange-calendar connection - only
// Postgres. "Next scheduled run" and "time until next session" below are
// computed from the bot's own known cron rule (see bot/scheduler.py:
// weekdays, :00/:30 marks, 09:00-16:00 America/New_York) and a plain
// Mon-Fri/9:30-16:00 regular-session assumption. Neither accounts for
// market holidays or early closes - both are disclosed as approximations
// in the UI, the same honesty pattern already used for the bot's own
// market-regime heuristic (see docs/strategy-intelligence-limitations.md).

function easternParts(d: Date): { weekday: number; minutesOfDay: number } {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const parts = fmt.formatToParts(d);
  const weekdayStr = parts.find((p) => p.type === "weekday")?.value ?? "Mon";
  const hourStr = parts.find((p) => p.type === "hour")?.value ?? "0";
  const minuteStr = parts.find((p) => p.type === "minute")?.value ?? "0";
  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const weekday = WEEKDAYS.indexOf(weekdayStr);
  // Intl gives "24" for midnight in some locales/environments instead of "00".
  const hour = Number(hourStr) % 24;
  const minute = Number(minuteStr);
  return { weekday, minutesOfDay: hour * 60 + minute };
}

const SESSION_OPEN_MIN = 9 * 60 + 30;
const SESSION_CLOSE_MIN = 16 * 60;
const SCHEDULER_START_MIN = 9 * 60;
const SCHEDULER_END_MIN = 16 * 60;

function isWeekday(weekday: number): boolean {
  return weekday >= 1 && weekday <= 5;
}

/** Rough current-session label + minutes until the next regular session
 * opens (null once already in a session). Approximate - see note above. */
function marketSessionInfo(now: Date): { sessionLabel: string; minutesUntilNextOpen: number | null } {
  const { weekday, minutesOfDay } = easternParts(now);
  if (isWeekday(weekday) && minutesOfDay >= SESSION_OPEN_MIN && minutesOfDay < SESSION_CLOSE_MIN) {
    return { sessionLabel: "Regular session", minutesUntilNextOpen: null };
  }
  // Walk forward day-by-day (at most 7) to the next weekday's open.
  let daysAhead = 0;
  let minutesUntil: number;
  if (isWeekday(weekday) && minutesOfDay < SESSION_OPEN_MIN) {
    minutesUntil = SESSION_OPEN_MIN - minutesOfDay;
  } else {
    daysAhead = 1;
    let w = (weekday + 1) % 7;
    while (!isWeekday(w)) {
      daysAhead++;
      w = (w + 1) % 7;
    }
    minutesUntil = (daysAhead - 1) * 24 * 60 + (24 * 60 - minutesOfDay) + SESSION_OPEN_MIN;
  }
  const label = isWeekday(weekday) && minutesOfDay >= SESSION_CLOSE_MIN ? "After hours" : "Closed";
  return { sessionLabel: label, minutesUntilNextOpen: Math.max(0, Math.round(minutesUntil)) };
}

/** Next :00/:30 the scheduler will fire, approximating bot/scheduler.py's
 * cron (weekdays, run_minutes=[0,30] by default, 09:00-16:00 ET). Doesn't
 * know about custom SCHEDULE_RUN_MINUTES overrides or market holidays -
 * disclosed as an estimate wherever this is shown in the UI. */
function nextScheduledRunIso(now: Date): string {
  const { weekday, minutesOfDay } = easternParts(now);
  const withinWindow = isWeekday(weekday) && minutesOfDay >= SCHEDULER_START_MIN && minutesOfDay <= SCHEDULER_END_MIN;

  if (withinWindow) {
    // Minutes until the next :00 or :30 mark, minimum 1 to avoid a 0-minute
    // "next run" reading identically to "just ran".
    const minutesFromNow = minutesOfDay % 30 === 0 ? 30 : 30 - (minutesOfDay % 30);
    return new Date(now.getTime() + minutesFromNow * 60_000).toISOString();
  }

  // Outside the window: walk forward to the next weekday's 09:00 ET.
  // Expressed as "minutes until midnight tonight, plus N full days, plus
  // 9:00" so it's correct regardless of how far into the weekend/evening
  // "now" currently is.
  let daysAhead = isWeekday(weekday) && minutesOfDay < SCHEDULER_START_MIN ? 0 : 1;
  let candidateWeekday = (weekday + daysAhead) % 7;
  while (!isWeekday(candidateWeekday)) {
    daysAhead++;
    candidateWeekday = (weekday + daysAhead) % 7;
  }
  const minutesUntilMidnight = 24 * 60 - minutesOfDay;
  const totalMinutes =
    daysAhead === 0
      ? SCHEDULER_START_MIN - minutesOfDay
      : minutesUntilMidnight + (daysAhead - 1) * 24 * 60 + SCHEDULER_START_MIN;
  return new Date(now.getTime() + totalMinutes * 60_000).toISOString();
}

type Heartbeat = {
  ts: string;
  status: string;
  scheduler_status: string;
  market_open: boolean | null;
  dry_run: boolean | null;
  portfolio_value: number | null;
  cash: number | null;
  equity: number | null;
  open_positions: number | null;
};

type LastTrade = {
  ts: string;
  action: string;
  symbol: string;
  qty: number | null;
  price: number | null;
};

type LatestDecision = {
  ts: string;
  symbol: string;
  decision: string;
  reason: string | null;
  sentiment_score: number | null;
  sentiment_label: string | null;
  rationale: string | null;
};

function closestSnapshot(
  series: { key: string; value: number }[],
  targetMsAgo: number
): number | null {
  if (series.length === 0) return null;
  const targetTs = Date.now() - targetMsAgo;
  let best: { value: number; diff: number } | null = null;
  for (const point of series) {
    const ts = new Date(point.key).getTime();
    const diff = Math.abs(ts - targetTs);
    if (!best || diff < best.diff) best = { value: point.value, diff };
  }
  return best?.value ?? null;
}

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const [
      heartbeat,
      lastTrade,
      latestDecision,
      activeVersion,
      pendingRecs,
      snapshots,
      todaysClosedTrades,
      allTimeClosedTrades,
      openPositionsAgg,
      largestPosition,
      todayFirstSnapshot,
      recentSchedulerFailure,
      recentBrokerIssue,
      recentDbFailure,
      botControl,
      recentAlerts,
    ] = await Promise.all([
      queryOne<Heartbeat>(
        `SELECT ts, status, scheduler_status, market_open, dry_run, portfolio_value, cash, equity, open_positions
         FROM heartbeats ORDER BY ts DESC LIMIT 1`
      ),
      queryOne<LastTrade>(
        `SELECT ts, action, symbol, qty, price FROM trades ORDER BY ts DESC LIMIT 1`
      ),
      queryOne<LatestDecision>(
        `SELECT ts, symbol, decision, reason, sentiment_score, sentiment_label, rationale
         FROM decisions WHERE decision IN ('buy','sell','buy_skipped','buy_blocked','scan')
         ORDER BY ts DESC LIMIT 1`
      ),
      queryOne<{ version: string }>(
        "SELECT version FROM strategy_versions WHERE is_active = true ORDER BY deployed_at DESC LIMIT 1"
      ),
      queryOne<{ count: string }>(
        "SELECT count(*) FROM strategy_recommendations WHERE status = 'pending'"
      ),
      query<{ ts: string; portfolio_value: number | null }>(
        "SELECT ts, portfolio_value FROM portfolio_snapshots ORDER BY ts ASC LIMIT 5000"
      ),
      queryOne<{ count: string; wins: string; total_pnl: string | null }>(
        `SELECT count(*), count(*) FILTER (WHERE pnl > 0) AS wins, sum(pnl) as total_pnl
         FROM closed_trades WHERE ts >= date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc'`
      ),
      query<ClosedTradeRow>(
        `SELECT symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                entry_time, ts, buy_reason, sector, confidence_score, confidence_label,
                market_regime, strategy_version
         FROM closed_trades ORDER BY ts ASC LIMIT 20000`
      ),
      query<{ count: string; total_unrealized: string | null }>(
        "SELECT count(*), sum(unrealized_pl) as total_unrealized FROM open_positions"
      ),
      queryOne<{ symbol: string; allocation_pct: number | null }>(
        "SELECT symbol, allocation_pct FROM open_positions ORDER BY allocation_pct DESC NULLS LAST LIMIT 1"
      ),
      queryOne<{ portfolio_value: number | null }>(
        `SELECT portfolio_value FROM portfolio_snapshots
         WHERE ts >= date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc'
         ORDER BY ts ASC LIMIT 1`
      ),
      queryOne<{ ts: string }>(
        "SELECT ts FROM notifications WHERE type = 'scheduler_failure' AND ts >= now() - interval '60 minutes' ORDER BY ts DESC LIMIT 1"
      ),
      queryOne<{ ts: string }>(
        "SELECT ts FROM notifications WHERE type = 'broker_issue' AND ts >= now() - interval '60 minutes' ORDER BY ts DESC LIMIT 1"
      ),
      queryOne<{ ts: string }>(
        "SELECT ts FROM notifications WHERE type = 'database_failure' AND ts >= now() - interval '60 minutes' ORDER BY ts DESC LIMIT 1"
      ),
      queryOne<{ is_paused: boolean; reason: string | null }>(
        "SELECT is_paused, reason FROM bot_control WHERE id = 1"
      ),
      query<{ id: number; ts: string; severity: string; title: string; message: string | null }>(
        `SELECT id, ts, severity, title, message FROM notifications
         WHERE severity IN ('critical','warning') AND ts >= now() - interval '24 hours'
         ORDER BY ts DESC LIMIT 10`
      ),
    ]);

    const now = new Date();
    const heartbeatAgeMinutes = heartbeat
      ? (now.getTime() - new Date(heartbeat.ts).getTime()) / 60000
      : null;
    const botRunning =
      Boolean(heartbeat) && heartbeatAgeMinutes !== null && heartbeatAgeMinutes < STALE_HEARTBEAT_MINUTES;
    const botStatus: "running" | "stopped" | "error" = !botRunning
      ? "stopped"
      : heartbeat!.status === "error" || Boolean(recentSchedulerFailure)
      ? "error"
      : "running";

    // ---- Portfolio ----
    const dailySeries = collapseToLastPerDay(snapshots);
    const portfolioValue = heartbeat?.portfolio_value ?? null;
    const weeklyBaseline = closestSnapshot(dailySeries, 7 * 86_400_000);
    const monthlyBaseline = closestSnapshot(dailySeries, 30 * 86_400_000);
    const lifetimeBaseline = dailySeries[0]?.value ?? null;
    const pctReturn = (baseline: number | null) =>
      baseline && portfolioValue != null && baseline > 0
        ? ((portfolioValue - baseline) / baseline) * 100
        : null;

    const realizedToday = todaysClosedTrades?.total_pnl ? Number(todaysClosedTrades.total_pnl) : 0;
    const unrealizedNow = openPositionsAgg[0]?.total_unrealized
      ? Number(openPositionsAgg[0].total_unrealized)
      : 0;
    const todaysPl = realizedToday + unrealizedNow;

    // ---- Trading activity ----
    const tradesToday = todaysClosedTrades ? Number(todaysClosedTrades.count) : 0;
    const winRatePct =
      allTimeClosedTrades.length > 0
        ? (allTimeClosedTrades.filter((t) => Number(t.pnl) > 0).length / allTimeClosedTrades.length) * 100
        : null;

    // ---- Risk snapshot ----
    const startOfDayValue = todayFirstSnapshot?.portfolio_value ?? null;
    const dailyPnlPct =
      startOfDayValue && startOfDayValue > 0 && portfolioValue != null
        ? ((portfolioValue - startOfDayValue) / startOfDayValue) * 100
        : null;
    const dailyLossBreached = dailyPnlPct != null && dailyPnlPct <= -riskConfig.dailyLossLimitPct;
    const totalExposurePct =
      portfolioValue && portfolioValue > 0
        ? ((portfolioValue - (heartbeat?.cash ?? 0)) / portfolioValue) * 100
        : null;
    const drawdownPct = maxDrawdownPct(dailySeries.map((p) => p.value));

    let riskLevel: "normal" | "elevated" | "high" = "normal";
    if (
      dailyLossBreached ||
      (totalExposurePct != null && totalExposurePct > riskConfig.maxTotalExposurePct)
    ) {
      riskLevel = "high";
    } else if (
      (dailyPnlPct != null && dailyPnlPct <= -0.7 * riskConfig.dailyLossLimitPct) ||
      (totalExposurePct != null && totalExposurePct > 0.8 * riskConfig.maxTotalExposurePct)
    ) {
      riskLevel = "elevated";
    }

    // ---- Market status ----
    const { sessionLabel, minutesUntilNextOpen } = marketSessionInfo(now);

    // ---- Alerts (merged feed: risk + system + recommendations) ----
    type Alert = { severity: "critical" | "warning" | "info"; message: string };
    const alerts: Alert[] = [];
    if (dailyLossBreached) {
      alerts.push({
        severity: "critical",
        message: `Daily loss limit hit (${riskConfig.dailyLossLimitPct}%) - new buys paused until tomorrow.`,
      });
    }
    if (recentBrokerIssue) {
      alerts.push({ severity: "warning", message: "Broker connectivity issue in the last hour - see System Health." });
    }
    if (recentDbFailure) {
      alerts.push({ severity: "warning", message: "Dashboard database write failures in the last hour (trading itself is unaffected)." });
    }
    if (recentSchedulerFailure) {
      alerts.push({ severity: "critical", message: "Scheduler failure in the last hour - see System Health." });
    }
    if (botControl?.is_paused) {
      alerts.push({
        severity: "warning",
        message: `Trading is paused${botControl.reason ? `: ${botControl.reason}` : ""}.`,
      });
    }
    const pendingCount = pendingRecs ? Number(pendingRecs.count) : 0;
    if (pendingCount > 0) {
      alerts.push({
        severity: "info",
        message: `${pendingCount} recommendation${pendingCount === 1 ? "" : "s"} awaiting your review.`,
      });
    }
    for (const n of recentAlerts) {
      alerts.push({ severity: n.severity as Alert["severity"], message: n.title });
    }

    return NextResponse.json({
      hasAnyData: Boolean(heartbeat),
      statusStrip: {
        bot: botStatus,
        market: heartbeat?.market_open ? "open" : "closed",
        risk: riskLevel,
        todaysPl,
      },
      portfolio: {
        value: portfolioValue,
        cash: heartbeat?.cash ?? null,
        todaysPl,
        weeklyReturnPct: pctReturn(weeklyBaseline),
        monthlyReturnPct: pctReturn(monthlyBaseline),
        lifetimeReturnPct: pctReturn(lifetimeBaseline),
      },
      tradingActivity: {
        openPositions: heartbeat?.open_positions ?? 0,
        openOrders: null, // N/A - this bot's model doesn't track resting limit orders; see Orders (future page)
        lastTrade: lastTrade
          ? { ts: lastTrade.ts, action: lastTrade.action, symbol: lastTrade.symbol, qty: lastTrade.qty, price: lastTrade.price }
          : null,
        tradesToday,
        winRatePct,
      },
      botStatusPanel: {
        running: botRunning,
        lastHeartbeat: heartbeat?.ts ?? null,
        schedulerStatus: heartbeat?.scheduler_status ?? null,
        dryRun: heartbeat?.dry_run ?? null,
        // "Last successful run" = last heartbeat, since one is written every
        // cycle unconditionally (even on a market-closed skip).
        lastSuccessfulRun: heartbeat?.ts ?? null,
        // Heuristic - see marketSessionInfo/nextScheduledRunIso notes above.
        nextScheduledRun: nextScheduledRunIso(now),
      },
      marketStatus: {
        open: heartbeat?.market_open ?? null,
        sessionLabel,
        minutesUntilNextOpen,
      },
      aiActivity: {
        latestDecision: latestDecision
          ? {
              ts: latestDecision.ts,
              symbol: latestDecision.symbol,
              decision: latestDecision.decision,
              reason: latestDecision.reason,
              confidence: latestDecision.sentiment_score,
              rationale: latestDecision.rationale,
            }
          : null,
        activeStrategyVersion: activeVersion?.version ?? "v1",
        marketSentimentLabel: latestDecision?.sentiment_label ?? null,
      },
      riskSnapshot: {
        totalExposurePct,
        largestPosition: largestPosition
          ? { symbol: largestPosition.symbol, allocationPct: largestPosition.allocation_pct }
          : null,
        drawdownPct,
        riskLevel,
        dailyLossLimitPct: riskConfig.dailyLossLimitPct,
        dailyPnlPct,
      },
      alerts,
      botControl: {
        isPaused: botControl?.is_paused ?? false,
        reason: botControl?.reason ?? null,
      },
    });
  } catch (err) {
    console.error("GET /api/home failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
