import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";

export const dynamic = "force-dynamic";

// Mirrors the threshold used on the Home page's /api/status - the scheduler
// runs every 30 min during market hours, so a longer gap than this means
// something's actually wrong (or the market's closed, which is handled
// separately via market_open).
const STALE_HEARTBEAT_MINUTES = 45;
// Notifications this recent are still considered "active" for a
// connectivity/failure banner, even if the very latest heartbeat looks fine.
const RECENT_ISSUE_WINDOW_MINUTES = 60;

type Heartbeat = {
  ts: string;
  status: string;
  scheduler_status: string;
  market_open: boolean | null;
  dry_run: boolean | null;
  api_latency_ms: number | null;
  trading_mode: string | null;
  daytrade_count: number | null;
  pattern_day_trader: boolean | null;
};

type DeployMeta = {
  ts: string;
  metadata: {
    commit?: string | null;
    commit_short?: string | null;
    service?: string | null;
    environment?: string | null;
    deployed_at?: string | null;
  } | null;
};

type RecentNotification = { ts: string; title: string; message: string | null };

function minutesAgo(ts: string | null): number | null {
  if (!ts) return null;
  return (Date.now() - new Date(ts).getTime()) / 60000;
}

const RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2";
const RAILWAY_REQUEST_TIMEOUT_MS = 8000;

type RailwayServiceStatus = {
  id: string;
  name: string;
  status: string | null; // raw Railway DeploymentStatus enum value, e.g. "SUCCESS", "BUILDING"
  deploymentId: string | null;
  updatedAt: string | null;
};

type RailwaySnapshot =
  | { configured: false; error?: undefined; services?: undefined }
  | { configured: true; error: string; services?: undefined }
  | { configured: true; error?: undefined; services: RailwayServiceStatus[] };

// Live per-service deploy/build status straight from Railway's public
// GraphQL API. RAILWAY_PROJECT_ID and RAILWAY_ENVIRONMENT_ID are injected
// automatically into this service's own runtime by Railway - the only
// manual setup is generating a token (railway.com/account/tokens, an
// "Account" or "Workspace" token both work) and setting it as
// RAILWAY_API_TOKEN on this dashboard service. Walking the whole project's
// services (rather than hardcoding this bot's two service IDs) means it
// keeps working unchanged if a service is renamed or a new one is added.
async function getRailwayStatus(): Promise<RailwaySnapshot> {
  const token = process.env.RAILWAY_API_TOKEN;
  const projectId = process.env.RAILWAY_PROJECT_ID;
  const environmentId = process.env.RAILWAY_ENVIRONMENT_ID;

  if (!token || !projectId || !environmentId) {
    // Deliberately not an error: this is an intentionally-optional feature.
    // projectId/environmentId missing almost always just means we're
    // running outside Railway (e.g. local dev), not a misconfiguration.
    return { configured: false };
  }

  const query = `
    query railwaySnapshot($projectId: String!) {
      project(id: $projectId) {
        services {
          edges {
            node {
              id
              name
              serviceInstances {
                edges {
                  node {
                    environmentId
                    latestDeployment {
                      id
                      status
                      updatedAt
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  `;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), RAILWAY_REQUEST_TIMEOUT_MS);

  try {
    const res = await fetch(RAILWAY_API_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query, variables: { projectId } }),
      signal: controller.signal,
      cache: "no-store",
    });

    const json = await res.json();

    if (json?.errors?.length) {
      const message = json.errors[0]?.message ?? "Railway API request failed";
      // A denied/expired token surfaces here as a normal 200 + errors array
      // (see Railway's GraphQL error convention), not an HTTP error status.
      return { configured: true, error: message };
    }

    type ServiceEdge = {
      node: {
        id: string;
        name: string;
        serviceInstances?: {
          edges?: Array<{
            node?: {
              environmentId?: string | null;
              latestDeployment?: { id?: string; status?: string; updatedAt?: string } | null;
            };
          }>;
        };
      };
    };
    const edges: ServiceEdge[] = json?.data?.project?.services?.edges ?? [];

    const services: RailwayServiceStatus[] = edges.map(({ node }) => {
      const instance = (node.serviceInstances?.edges ?? []).find(
        (e) => e.node?.environmentId === environmentId
      )?.node;
      return {
        id: node.id,
        name: node.name,
        status: instance?.latestDeployment?.status ?? null,
        deploymentId: instance?.latestDeployment?.id ?? null,
        updatedAt: instance?.latestDeployment?.updatedAt ?? null,
      };
    });

    return { configured: true, services };
  } catch (err) {
    const message =
      err instanceof Error
        ? err.name === "AbortError"
          ? "Railway API request timed out"
          : err.message
        : "Railway API request failed";
    return { configured: true, error: message };
  } finally {
    clearTimeout(timeout);
  }
}

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const dbCallStarted = Date.now();
  try {
    // Kicked off alongside the DB batch below (not awaited until the very
    // end) so a slow Railway API response doesn't add its full latency on
    // top of the DB round-trip - the two happen concurrently.
    const railwayPromise = getRailwayStatus();

    const [heartbeat, lastFullHeartbeat, lastScan, lastDeploy, lastBrokerIssue, lastSchedulerFailure, lastDbFailure] =
      await Promise.all([
        queryOne<Heartbeat>(
          "SELECT ts, status, scheduler_status, market_open, dry_run, api_latency_ms, trading_mode, daytrade_count, pattern_day_trader FROM heartbeats ORDER BY ts DESC LIMIT 1"
        ),
        // trading_mode / daytrade_count / pattern_day_trader / api_latency_ms
        // are only populated on a heartbeat written after a successful
        // Alpaca account_snapshot() call - the market-closed early-return
        // heartbeat (see bot/trading/strategy.py's run_cycle) skips that
        // call entirely, leaving these NULL on that row. Since the market
        // is closed ~81% of the week, reading only "the latest heartbeat"
        // would blank these out most of the time. This fallback query gets
        // the latest heartbeat that DOES have a portfolio_value (i.e. ran
        // the real account call), so these fields show the last genuinely
        // known reading instead of going blank on a quiet evening/weekend.
        queryOne<Heartbeat>(
          "SELECT ts, status, scheduler_status, market_open, dry_run, api_latency_ms, trading_mode, daytrade_count, pattern_day_trader FROM heartbeats WHERE portfolio_value IS NOT NULL ORDER BY ts DESC LIMIT 1"
        ),
        queryOne<{ ts: string }>(
          "SELECT ts FROM decisions WHERE decision = 'scan' ORDER BY ts DESC LIMIT 1"
        ),
        queryOne<DeployMeta>(
          "SELECT ts, metadata FROM notifications WHERE type = 'deployment_completed' ORDER BY ts DESC LIMIT 1"
        ),
        queryOne<RecentNotification>(
          `SELECT ts, title, message FROM notifications
           WHERE type = 'broker_issue' AND ts >= now() - interval '${RECENT_ISSUE_WINDOW_MINUTES} minutes'
           ORDER BY ts DESC LIMIT 1`
        ),
        queryOne<RecentNotification>(
          `SELECT ts, title, message FROM notifications
           WHERE type = 'scheduler_failure' AND ts >= now() - interval '${RECENT_ISSUE_WINDOW_MINUTES} minutes'
           ORDER BY ts DESC LIMIT 1`
        ),
        queryOne<RecentNotification>(
          `SELECT ts, title, message FROM notifications
           WHERE type = 'database_failure' AND ts >= now() - interval '${RECENT_ISSUE_WINDOW_MINUTES} minutes'
           ORDER BY ts DESC LIMIT 1`
        ),
      ]);
    const dbLatencyMs = Date.now() - dbCallStarted;
    const railway = await railwayPromise;

    const heartbeatAgeMinutes = minutesAgo(heartbeat?.ts ?? null);
    const heartbeatStale =
      heartbeatAgeMinutes == null || heartbeatAgeMinutes > STALE_HEARTBEAT_MINUTES;

    // Bot / scheduler status: recency of any heartbeat is itself proof the
    // scheduler is alive (a heartbeat is written every cycle, market open or
    // not), independent of whether the trading cycle itself hit an error.
    let botStatus: "running" | "stopped" | "error";
    if (!heartbeat || heartbeatStale) {
      botStatus = "stopped";
    } else if (heartbeat.status === "error" || lastSchedulerFailure) {
      botStatus = "error";
    } else {
      botStatus = "running";
    }

    // Alpaca connectivity: a heartbeat with status='error' is written
    // specifically when the account snapshot call fails (see
    // bot/trading/strategy.py) - that's the most direct signal. A recent
    // broker_issue notification is a fallback in case the very latest
    // heartbeat happened to recover in between.
    let alpacaStatus: "connected" | "error" | "unknown";
    if (!heartbeat) {
      alpacaStatus = "unknown";
    } else if (heartbeat.status === "error" || (lastBrokerIssue && !heartbeatStale)) {
      alpacaStatus = "error";
    } else if (heartbeatStale) {
      alpacaStatus = "unknown";
    } else {
      alpacaStatus = "connected";
    }

    const schedulerStatus: "running" | "stopped" | "error" = lastSchedulerFailure
      ? "error"
      : heartbeatStale
      ? "stopped"
      : "running";

    return NextResponse.json({
      hasAnyData: Boolean(heartbeat),
      heartbeat: {
        lastTs: heartbeat?.ts ?? null,
        ageMinutes: heartbeatAgeMinutes,
        marketOpen: heartbeat?.market_open ?? null,
        dryRun: heartbeat?.dry_run ?? null,
        tradingMode: heartbeat?.trading_mode ?? lastFullHeartbeat?.trading_mode ?? null,
        daytradeCount: heartbeat?.daytrade_count ?? lastFullHeartbeat?.daytrade_count ?? null,
        patternDayTrader: heartbeat?.pattern_day_trader ?? lastFullHeartbeat?.pattern_day_trader ?? null,
      },
      botStatus,
      scheduler: {
        status: schedulerStatus,
        lastMode: heartbeat?.scheduler_status ?? null,
        lastFailure: lastSchedulerFailure
          ? { ts: lastSchedulerFailure.ts, message: lastSchedulerFailure.message }
          : null,
      },
      alpaca: {
        status: alpacaStatus,
        apiLatencyMs: heartbeat?.api_latency_ms ?? lastFullHeartbeat?.api_latency_ms ?? null,
        lastIssue: lastBrokerIssue
          ? { ts: lastBrokerIssue.ts, message: lastBrokerIssue.message }
          : null,
      },
      database: {
        status: "connected", // if we got here at all, this query itself succeeded
        queryLatencyMs: dbLatencyMs,
        lastFailure: lastDbFailure
          ? { ts: lastDbFailure.ts, message: lastDbFailure.message }
          : null,
      },
      lastSuccessfulDbWrite: heartbeat?.ts ?? null, // heartbeats are written every cycle unconditionally
      lastSuccessfulMarketScan: lastScan?.ts ?? null,
      deployment: {
        commit: lastDeploy?.metadata?.commit ?? null,
        commitShort: lastDeploy?.metadata?.commit_short ?? null,
        environment: lastDeploy?.metadata?.environment ?? null,
        deployedAt: lastDeploy?.metadata?.deployed_at ?? lastDeploy?.ts ?? null,
        appVersion: lastDeploy?.metadata?.commit_short ?? null,
      },
      railway,
    });
  } catch (err) {
    console.error("GET /api/system-health failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
