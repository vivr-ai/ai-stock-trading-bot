"use client";

import { useEffect, useState } from "react";
import StatCard from "@/components/StatCard";
import StatusBadge, { Status } from "@/components/StatusBadge";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { timeAgo } from "@/lib/format";
import {
  HeartPulse,
  Server,
  Database,
  Zap,
  CalendarClock,
  Radar,
  Save,
  Gauge,
  Tag,
  GitCommit,
  ShieldAlert,
} from "lucide-react";

type SystemHealthResponse = {
  hasAnyData: boolean;
  heartbeat: {
    lastTs: string | null;
    ageMinutes: number | null;
    marketOpen: boolean | null;
    dryRun: boolean | null;
    tradingMode: string | null;
    daytradeCount: number | null;
    patternDayTrader: boolean | null;
  };
  botStatus: "running" | "stopped" | "error";
  scheduler: {
    status: "running" | "stopped" | "error";
    lastMode: string | null;
    lastFailure: { ts: string; message: string | null } | null;
  };
  alpaca: {
    status: "connected" | "error" | "unknown";
    apiLatencyMs: number | null;
    lastIssue: { ts: string; message: string | null } | null;
  };
  database: {
    status: "connected";
    queryLatencyMs: number;
    lastFailure: { ts: string; message: string | null } | null;
  };
  lastSuccessfulDbWrite: string | null;
  lastSuccessfulMarketScan: string | null;
  deployment: {
    commit: string | null;
    commitShort: string | null;
    environment: string | null;
    deployedAt: string | null;
    appVersion: string | null;
  };
  railway:
    | { configured: false }
    | { configured: true; error: string; services?: undefined }
    | {
        configured: true;
        error?: undefined;
        services: Array<{
          id: string;
          name: string;
          status: string | null;
          deploymentId: string | null;
          updatedAt: string | null;
        }>;
      };
};

// Maps Railway's DeploymentStatus enum to this dashboard's own badge tones.
// See https://docs.railway.com/integrations/api/manage-deployments#deployment-statuses
function railwayStatusBadge(status: string | null): Status {
  switch (status) {
    case "SUCCESS":
      return "running";
    case "BUILDING":
    case "DEPLOYING":
    case "QUEUED":
    case "WAITING":
      return "building";
    case "FAILED":
    case "CRASHED":
      return "error";
    case "SLEEPING":
      return "paused";
    case "REMOVED":
    case "SKIPPED":
      return "stopped";
    default:
      return "unknown";
  }
}

function railwayStatusLabel(status: string | null): string {
  if (!status) return "Unknown";
  // "BUILDING" -> "Building", "CRASHED" -> "Crashed", etc.
  return status.charAt(0) + status.slice(1).toLowerCase();
}

export default function SystemHealthPage() {
  const [data, setData] = useState<SystemHealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const res = await fetch("/api/system-health");
      if (!res.ok) throw new Error((await res.json()).error || "Request failed");
      setData(await res.json());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000); // this page is meant for quick checks, refresh often
    return () => clearInterval(id);
  }, []);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">System Health</h1>
        <p className="text-sm text-muted">Infrastructure and connectivity status, separate from trading performance.</p>
      </div>

      {loading && <LoadingSkeleton rows={8} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && !data.hasAnyData && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No data yet. This fills in once the bot has run at least one cycle.
        </div>
      )}

      {!loading && !error && data && data.hasAnyData && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <StatCard
              label="Bot Heartbeat"
              value={<StatusBadge status={data.botStatus} />}
              sublabel={
                data.heartbeat.lastTs
                  ? `Last beat ${timeAgo(data.heartbeat.lastTs)}`
                  : "No heartbeat recorded yet"
              }
              icon={<HeartPulse size={15} />}
            />
            {data.railway.configured && data.railway.services ? (
              data.railway.services.map((svc) => (
                <StatCard
                  key={svc.id}
                  label={`Railway: ${svc.name}`}
                  value={
                    <StatusBadge
                      status={railwayStatusBadge(svc.status)}
                      label={railwayStatusLabel(svc.status)}
                    />
                  }
                  sublabel={svc.updatedAt ? `Deployed ${timeAgo(svc.updatedAt)}` : "No deployment recorded"}
                  icon={<Server size={15} />}
                />
              ))
            ) : (
              <StatCard
                label="Railway Service Status"
                value={
                  data.railway.configured ? (
                    <StatusBadge status="error" label="API error" />
                  ) : (
                    <StatusBadge status="not_configured" />
                  )
                }
                sublabel={
                  data.railway.configured
                    ? data.railway.error ?? "Railway API request failed"
                    : "Needs a Railway API token - not set up yet"
                }
                icon={<Server size={15} />}
              />
            )}
            <StatCard
              label="Database Connectivity"
              value={<StatusBadge status={data.database.status} />}
              sublabel={
                data.database.lastFailure
                  ? `Last failure ${timeAgo(data.database.lastFailure.ts)}`
                  : "No recent failures"
              }
              icon={<Database size={15} />}
            />
            <StatCard
              label="Alpaca Connectivity"
              value={<StatusBadge status={data.alpaca.status} />}
              sublabel={
                data.alpaca.lastIssue
                  ? `Last issue ${timeAgo(data.alpaca.lastIssue.ts)}`
                  : "No recent failures"
              }
              icon={<Zap size={15} />}
            />
            <StatCard
              label="Scheduler Status"
              value={<StatusBadge status={data.scheduler.status} />}
              sublabel={
                data.scheduler.lastFailure
                  ? `Last crash ${timeAgo(data.scheduler.lastFailure.ts)}`
                  : `Mode: ${data.scheduler.lastMode ?? "—"}`
              }
              icon={<CalendarClock size={15} />}
            />
            <StatCard
              label="Market Status"
              value={
                data.heartbeat.marketOpen == null
                  ? "—"
                  : data.heartbeat.marketOpen
                  ? "Open"
                  : "Closed"
              }
              tone={data.heartbeat.marketOpen ? "gain" : "neutral"}
              sublabel={data.heartbeat.dryRun ? "Dry run mode" : "Live paper trading"}
              icon={<Radar size={15} />}
            />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <StatCard
              label="Last Successful Market Scan"
              value={timeAgo(data.lastSuccessfulMarketScan)}
              sublabel={
                data.lastSuccessfulMarketScan
                  ? new Date(data.lastSuccessfulMarketScan).toLocaleString()
                  : "No scans recorded yet"
              }
              icon={<Radar size={15} />}
            />
            <StatCard
              label="Last Successful Database Write"
              value={timeAgo(data.lastSuccessfulDbWrite)}
              sublabel={
                data.lastSuccessfulDbWrite
                  ? new Date(data.lastSuccessfulDbWrite).toLocaleString()
                  : "No writes recorded yet"
              }
              icon={<Save size={15} />}
            />
            <StatCard
              label="API Response Latency"
              value={
                data.alpaca.apiLatencyMs != null
                  ? `${Math.round(data.alpaca.apiLatencyMs)} ms`
                  : "—"
              }
              sublabel={`Alpaca account snapshot, most recent cycle · DB query took ${data.database.queryLatencyMs}ms`}
              icon={<Gauge size={15} />}
            />
            <StatCard
              label="Current Application Version"
              value={data.deployment.appVersion ?? "—"}
              sublabel="Short commit hash of the deployed bot code"
              icon={<Tag size={15} />}
            />
            <StatCard
              label="Last Deployed Git Commit"
              value={data.deployment.commitShort ?? "—"}
              sublabel={
                data.deployment.deployedAt
                  ? `${data.deployment.environment ?? ""} · ${timeAgo(data.deployment.deployedAt)}`
                  : "No deployment notifications recorded yet"
              }
              icon={<GitCommit size={15} />}
            />
            <StatCard
              label="Pattern Day Trader"
              value={
                data.heartbeat.daytradeCount == null
                  ? "—"
                  : data.heartbeat.patternDayTrader
                  ? "FLAGGED"
                  : `${data.heartbeat.daytradeCount}/4`
              }
              tone={
                data.heartbeat.patternDayTrader
                  ? "loss"
                  : (data.heartbeat.daytradeCount ?? 0) >= 3
                  ? "loss"
                  : "neutral"
              }
              sublabel={
                data.heartbeat.tradingMode
                  ? `mode: ${data.heartbeat.tradingMode} · see Live Readiness for detail`
                  : "Day trades in the current rolling 5-day window"
              }
              icon={<ShieldAlert size={15} />}
            />
          </div>

          {(data.alpaca.lastIssue || data.scheduler.lastFailure || data.database.lastFailure) && (
            <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
              <h2 className="mb-3 text-base font-semibold text-white">Recent issues (last hour)</h2>
              <div className="space-y-2 text-sm">
                {data.alpaca.lastIssue && (
                  <div className="rounded-lg border border-accent/40 bg-accent/10 p-3 text-accent">
                    Alpaca: {data.alpaca.lastIssue.message ?? "connectivity issue"}
                  </div>
                )}
                {data.scheduler.lastFailure && (
                  <div className="rounded-lg border border-loss/40 bg-loss/10 p-3 text-loss">
                    Scheduler: {data.scheduler.lastFailure.message ?? "crashed"}
                  </div>
                )}
                {data.database.lastFailure && (
                  <div className="rounded-lg border border-loss/40 bg-loss/10 p-3 text-loss">
                    Database: {data.database.lastFailure.message ?? "write failure"}
                  </div>
                )}
              </div>
            </div>
          )}

          {!data.railway.configured && (
            <div className="rounded-xl border border-dashed border-bg-border bg-bg-panel p-5 text-sm text-muted">
              Railway service status isn&apos;t connected yet - it needs a Railway API token, which
              you haven&apos;t set up. Once you have one, this can show live deploy/build status
              per service directly from Railway.
            </div>
          )}

          {data.railway.configured && data.railway.error && (
            <div className="rounded-lg border border-loss/40 bg-loss/10 p-3 text-sm text-loss">
              Railway: {data.railway.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
