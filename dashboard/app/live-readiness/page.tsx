"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import StatusBadge from "@/components/StatusBadge";
import { CheckCircle2, XCircle, AlertTriangle, CircleDashed, ShieldCheck } from "lucide-react";

type ChecklistItem = {
  id: string;
  label: string;
  detail: string;
  status: "pass" | "fail" | "warning" | "manual";
  automatic: boolean;
};

type LiveReadinessResponse = {
  currentMode: string | null;
  allowSubmit: boolean;
  configReportedAt: string | null;
  deployment: { commitShort: string | null; environment: string | null };
  readyForLive: boolean;
  checklist: ChecklistItem[];
};

function StatusIcon({ status }: { status: ChecklistItem["status"] }) {
  if (status === "pass") return <CheckCircle2 size={18} className="text-gain" />;
  if (status === "fail") return <XCircle size={18} className="text-loss" />;
  if (status === "warning") return <AlertTriangle size={18} className="text-accent" />;
  return <CircleDashed size={18} className="text-muted" />;
}

function modeLabel(mode: string | null): string {
  if (mode === "paper") return "PAPER";
  if (mode === "dry_run") return "DRY RUN (live account, no orders)";
  if (mode === "live") return "LIVE";
  return "Unknown";
}

export default function LiveReadinessPage() {
  const [data, setData] = useState<LiveReadinessResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const res = await fetch("/api/live-readiness");
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
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Live Readiness</h1>
        <p className="text-sm text-muted">
          Everything to check before enabling LIVE trading. Switching modes only ever needs Railway
          environment variables and a redeploy - no code changes.
        </p>
      </div>

      {loading && <LoadingSkeleton rows={4} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
              <div className="text-xs text-muted">Current operating mode</div>
              <div className="mt-2 text-2xl font-semibold text-white">{modeLabel(data.currentMode)}</div>
              <div className="mt-1 text-xs text-muted">
                {data.deployment.commitShort
                  ? `commit ${data.deployment.commitShort}${data.deployment.environment ? ` · ${data.deployment.environment}` : ""}`
                  : "No deployment metadata yet"}
              </div>
            </div>
            <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
              <div className="text-xs text-muted">Order submission</div>
              <div className="mt-2 text-2xl font-semibold text-white">
                {data.allowSubmit ? "Enabled" : "Disabled"}
              </div>
              <div className="mt-1 text-xs text-muted">
                {data.allowSubmit
                  ? "This deployment CAN submit real orders."
                  : "Orders are blocked at the broker layer in this deployment."}
              </div>
            </div>
            <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
              <div className="text-xs text-muted">Overall readiness</div>
              <div className="mt-2 flex items-center gap-2">
                <ShieldCheck size={20} className={data.readyForLive ? "text-gain" : "text-muted"} />
                <StatusBadge
                  status={data.readyForLive ? "connected" : "not_configured"}
                  label={data.readyForLive ? "Automatic checks pass" : "Not ready"}
                />
              </div>
              <div className="mt-1 text-xs text-muted">
                Automatic checks only - review the manual items below too.
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
            <h2 className="mb-1 text-base font-semibold text-white">Automatic checks</h2>
            <p className="mb-4 text-xs text-muted">
              Verified from what the bot has reported to the dashboard - it can&apos;t read Railway&apos;s
              environment variables directly, only what the bot itself observed at its last startup and
              heartbeat.
            </p>
            <div className="space-y-3">
              {data.checklist
                .filter((i) => i.automatic)
                .map((item) => (
                  <div key={item.id} className="flex items-start gap-3">
                    <div className="mt-0.5 shrink-0">
                      <StatusIcon status={item.status} />
                    </div>
                    <div>
                      <div className="text-sm font-medium text-white">{item.label}</div>
                      <div className="text-xs text-muted">{item.detail}</div>
                    </div>
                  </div>
                ))}
            </div>
          </div>

          <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
            <h2 className="mb-1 text-base font-semibold text-white">Manual checklist</h2>
            <p className="mb-4 text-xs text-muted">
              These can&apos;t be verified automatically - review each one yourself before flipping
              TRADING_MODE to live.
            </p>
            <div className="space-y-3">
              {data.checklist
                .filter((i) => !i.automatic)
                .map((item) => (
                  <div key={item.id} className="flex items-start gap-3">
                    <div className="mt-0.5 shrink-0">
                      <StatusIcon status={item.status} />
                    </div>
                    <div>
                      <div className="text-sm font-medium text-white">{item.label}</div>
                      <div className="text-xs text-muted">{item.detail}</div>
                    </div>
                  </div>
                ))}
            </div>
          </div>

          <div className="rounded-xl border border-dashed border-bg-border bg-bg-panel p-5 text-sm text-muted">
            <div className="mb-2 font-medium text-white">How to go live</div>
            <ol className="list-decimal space-y-1 pl-5">
              <li>Set <code className="text-white">ALPACA_LIVE_API_KEY</code> / <code className="text-white">ALPACA_LIVE_SECRET_KEY</code> in Railway.</li>
              <li>Set <code className="text-white">TRADING_MODE=dry_run</code> and redeploy - rehearse for a few trading days.</li>
              <li>When satisfied, set <code className="text-white">TRADING_MODE=live</code>, <code className="text-white">LIVE_TRADING_CONFIRMED=true</code>, and <code className="text-white">RISK_DRY_RUN=false</code>, then redeploy.</li>
              <li>All three must agree - missing any one keeps the bot in a safe, order-free state.</li>
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}
