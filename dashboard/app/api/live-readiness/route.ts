import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { queryOne } from "@/lib/db";

export const dynamic = "force-dynamic";

// Same staleness threshold as System Health - the scheduler runs every 30
// min during market hours, so config_status is only refreshed at startup,
// but heartbeats confirm the bot is still alive with that configuration.
const STALE_HEARTBEAT_MINUTES = 45;

type ConfigStatus = {
  ts: string;
  trading_mode: string;
  live_confirmed: boolean;
  risk_dry_run: boolean;
  allow_submit: boolean;
  has_paper_keys: boolean;
  has_live_keys: boolean;
  has_telegram: boolean;
  has_database: boolean;
  commit_short: string | null;
  environment: string | null;
};

type Heartbeat = {
  ts: string;
  daytrade_count: number | null;
  pattern_day_trader: boolean | null;
  portfolio_value: number | null;
  equity: number | null;
};

type ChecklistItem = {
  id: string;
  label: string;
  detail: string;
  status: "pass" | "fail" | "warning" | "manual";
  automatic: boolean;
};

function minutesAgo(ts: string | null): number | null {
  if (!ts) return null;
  return (Date.now() - new Date(ts).getTime()) / 60000;
}

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const [config, heartbeat] = await Promise.all([
      queryOne<ConfigStatus>(
        `SELECT ts, trading_mode, live_confirmed, risk_dry_run, allow_submit,
                has_paper_keys, has_live_keys, has_telegram, has_database,
                commit_short, environment
         FROM config_status ORDER BY ts DESC LIMIT 1`
      ),
      queryOne<Heartbeat>(
        `SELECT ts, daytrade_count, pattern_day_trader, portfolio_value, equity
         FROM heartbeats ORDER BY ts DESC LIMIT 1`
      ),
    ]);

    const heartbeatAgeMinutes = minutesAgo(heartbeat?.ts ?? null);
    const heartbeatStale = heartbeatAgeMinutes == null || heartbeatAgeMinutes > STALE_HEARTBEAT_MINUTES;

    const checklist: ChecklistItem[] = [];

    // ---- Automatic checks (from config_status / heartbeats) ----------------
    if (!config) {
      checklist.push({
        id: "config_status",
        label: "Bot configuration reported to dashboard",
        detail: "No config_status row yet - the bot needs to start at least once (any mode) " +
          "after this feature was deployed for the rest of this checklist to populate.",
        status: "fail",
        automatic: true,
      });
    } else {
      checklist.push({
        id: "paper_keys",
        label: "Alpaca PAPER credentials present",
        detail: config.has_paper_keys
          ? "ALPACA_API_KEY / ALPACA_SECRET_KEY are set."
          : "Missing ALPACA_API_KEY / ALPACA_SECRET_KEY.",
        status: config.has_paper_keys ? "pass" : "fail",
        automatic: true,
      });
      checklist.push({
        id: "live_keys",
        label: "Alpaca LIVE credentials present",
        detail: config.has_live_keys
          ? "ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY are set."
          : "Missing ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY - required before TRADING_MODE " +
            "can be dry_run or live.",
        status: config.has_live_keys ? "pass" : "fail",
        automatic: true,
      });
      checklist.push({
        id: "live_confirmed",
        label: "LIVE_TRADING_CONFIRMED set",
        detail: config.live_confirmed
          ? "Explicit live-trading confirmation is set."
          : "Not set - required (in addition to TRADING_MODE=live) before any real order can submit.",
        status: config.live_confirmed ? "pass" : "manual",
        automatic: true,
      });
      checklist.push({
        id: "risk_dry_run",
        label: "RISK_DRY_RUN gate",
        detail: config.risk_dry_run
          ? "Currently true - even in TRADING_MODE=live this blocks real order submission until " +
            "set to false."
          : "Currently false - this gate will NOT block live orders. Confirm this is intentional.",
        status: config.risk_dry_run ? "manual" : "warning",
        automatic: true,
      });
      checklist.push({
        id: "telegram",
        label: "Telegram notifications configured",
        detail: config.has_telegram
          ? "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are set - you'll be notified of trades and errors."
          : "Not configured - strongly recommended before going live so you hear about failures in " +
            "real time, not just from the dashboard.",
        status: config.has_telegram ? "pass" : "warning",
        automatic: true,
      });
      checklist.push({
        id: "database",
        label: "Dashboard database connectivity",
        detail: "Postgres is reachable (this page loaded from it).",
        status: "pass",
        automatic: true,
      });
      checklist.push({
        id: "bot_alive",
        label: "Bot process is alive and reporting",
        detail: heartbeatStale
          ? `No heartbeat in the last ${STALE_HEARTBEAT_MINUTES} minutes - the bot may be stopped ` +
            "or crash-looping. Check System Health / Railway logs."
          : "Recent heartbeat received.",
        status: heartbeatStale ? "fail" : "pass",
        automatic: true,
      });

      // PDT counter - informational unless already flagged or close to it.
      const daytradeCount = heartbeat?.daytrade_count ?? null;
      const patternDayTrader = heartbeat?.pattern_day_trader ?? false;
      const equity = heartbeat?.equity ?? null;
      const under25k = equity != null && equity < 25000;
      if (daytradeCount != null) {
        checklist.push({
          id: "pdt",
          label: "Pattern Day Trader status",
          detail: patternDayTrader
            ? `Account IS flagged as a Pattern Day Trader.${under25k ? " Equity is under $25,000 - new positions may be restricted." : ""}`
            : `${daytradeCount}/4 day trades in the current rolling 5-business-day window.${under25k ? " Equity is under $25,000, so reaching 4 triggers PDT restrictions." : ""}`,
          status: patternDayTrader ? "fail" : daytradeCount >= 3 ? "warning" : "pass",
          automatic: true,
        });
      }
    }

    // ---- Manual checklist items (can't be verified from Postgres alone) ----
    const manualItems: ChecklistItem[] = [
      {
        id: "w8ben",
        label: "W-8BEN form submitted to Alpaca",
        detail: "Reduces US dividend withholding tax under the US-Australia tax treaty. Submit via " +
          "Alpaca's account documents section before going live, if not already done.",
        status: "manual",
        automatic: false,
      },
      {
        id: "funding",
        label: "Live account funded to your intended starting size",
        detail: "Confirm the live Alpaca account balance matches what you intend to trade with - " +
          "the bot doesn't check this before placing orders.",
        status: "manual",
        automatic: false,
      },
      {
        id: "risk_limits_reviewed",
        label: "Risk limits reviewed for real money",
        detail: "RISK_MAX_POSITION_PCT, RISK_MAX_OPEN_POSITIONS, RISK_DAILY_LOSS_LIMIT_PCT, etc. were " +
          "tuned for paper trading - re-review these values before enabling LIVE mode.",
        status: "manual",
        automatic: false,
      },
      {
        id: "dry_run_rehearsed",
        label: "Rehearsed in DRY_RUN mode against the live account",
        detail: "Run TRADING_MODE=dry_run for at least a few trading days first - it evaluates every " +
          "decision against real account data without ever submitting an order.",
        status: "manual",
        automatic: false,
      },
      {
        id: "railway_redeploy",
        label: "Ready to redeploy after changing Railway variables",
        detail: "Changing TRADING_MODE / LIVE_TRADING_CONFIRMED / RISK_DRY_RUN requires a redeploy to " +
          "take effect - no code changes are ever needed, just the variables and a redeploy.",
        status: "manual",
        automatic: false,
      },
    ];

    const allItems = [...checklist, ...manualItems];
    const blockingFailures = allItems.filter((i) => i.status === "fail").length;
    const readyForLive =
      !!config &&
      config.has_live_keys &&
      config.live_confirmed &&
      !config.risk_dry_run &&
      !heartbeatStale &&
      blockingFailures === 0;

    return NextResponse.json({
      currentMode: config?.trading_mode ?? null,
      allowSubmit: config?.allow_submit ?? false,
      configReportedAt: config?.ts ?? null,
      deployment: { commitShort: config?.commit_short ?? null, environment: config?.environment ?? null },
      readyForLive,
      checklist: allItems,
    });
  } catch (err) {
    console.error("GET /api/live-readiness failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
