"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import StatusBadge from "@/components/StatusBadge";
import { fmtMoney, timeAgo } from "@/lib/format";
import { GitBranch, Plus, X } from "lucide-react";

type VersionMetrics = {
  totalTrades: number;
  winRatePct: number | null;
  profitFactor: number | null;
  expectancy: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  maxDrawdownPct?: number | null;
  totalReturn: number;
};

type Version = {
  version: string;
  deployed_at: string;
  description: string | null;
  config_snapshot: Record<string, unknown> | null;
  is_active: boolean;
  created_from_recommendation_id: number | null;
  metrics: VersionMetrics;
};

function MetricRow({ label, values }: { label: string; values: (string | number)[] }) {
  return (
    <tr className="border-t border-bg-border">
      <td className="py-2 pr-4 text-xs text-muted">{label}</td>
      {values.map((v, i) => (
        <td key={i} className="py-2 px-4 text-sm text-white">{v}</td>
      ))}
    </tr>
  );
}

export default function StrategyVersionsPage() {
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [newVersion, setNewVersion] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  async function load() {
    try {
      const res = await fetch("/api/strategy-versions");
      if (!res.ok) throw new Error((await res.json()).error || "Request failed");
      const json = await res.json();
      setVersions(json.versions);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function toggleSelect(version: string) {
    setSelected((prev) =>
      prev.includes(version)
        ? prev.filter((v) => v !== version)
        : prev.length < 2
        ? [...prev, version]
        : [prev[1], version]
    );
  }

  async function createVersion() {
    setCreating(true);
    setCreateError(null);
    try {
      const res = await fetch("/api/strategy-versions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: newVersion, description: newDescription }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Request failed");
      setShowCreate(false);
      setNewVersion("");
      setNewDescription("");
      await load();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create version");
    } finally {
      setCreating(false);
    }
  }

  const compareVersions = versions?.filter((v) => selected.includes(v.version)) ?? [];

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Strategy Versions</h1>
          <p className="text-sm text-muted">
            Every version the bot has run under, with performance while active. Select up to two to
            compare. Creating a new version is the only action that changes what new trades are
            tagged with going forward - it never edits the trading engine's code or config directly.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          <Plus size={15} /> New version
        </button>
      </div>

      {loading && <LoadingSkeleton rows={4} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && versions && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {versions.map((v) => (
              <button
                key={v.version}
                onClick={() => toggleSelect(v.version)}
                className={`rounded-xl border p-4 text-left transition-colors ${
                  selected.includes(v.version)
                    ? "border-accent bg-accent/10"
                    : "border-bg-border bg-bg-panel hover:border-accent/50"
                }`}
              >
                <div className="mb-1 flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm font-semibold text-white">
                    <GitBranch size={15} /> {v.version}
                  </div>
                  {v.is_active && <StatusBadge status="connected" label="Active" />}
                </div>
                <div className="text-xs text-muted">Deployed {timeAgo(v.deployed_at)}</div>
                {v.description && <div className="mt-2 text-xs text-muted">{v.description}</div>}
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <div className="text-muted">Trades</div>
                    <div className="text-white">{v.metrics.totalTrades}</div>
                  </div>
                  <div>
                    <div className="text-muted">Win rate</div>
                    <div className="text-white">
                      {v.metrics.winRatePct != null ? `${v.metrics.winRatePct.toFixed(1)}%` : "—"}
                    </div>
                  </div>
                  <div>
                    <div className="text-muted">Return</div>
                    <div className={v.metrics.totalReturn >= 0 ? "text-gain" : "text-loss"}>
                      {fmtMoney(v.metrics.totalReturn)}
                    </div>
                  </div>
                  <div>
                    <div className="text-muted">Profit factor</div>
                    <div className="text-white">
                      {v.metrics.profitFactor != null ? v.metrics.profitFactor.toFixed(2) : "—"}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>

          {compareVersions.length === 2 && (
            <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
              <h2 className="mb-3 text-base font-semibold text-white">Comparison</h2>
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="pb-2 text-left text-xs text-muted">Metric</th>
                    {compareVersions.map((v) => (
                      <th key={v.version} className="pb-2 px-4 text-left text-xs text-muted">
                        {v.version}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <MetricRow
                    label="Trades"
                    values={compareVersions.map((v) => v.metrics.totalTrades)}
                  />
                  <MetricRow
                    label="Win rate"
                    values={compareVersions.map((v) =>
                      v.metrics.winRatePct != null ? `${v.metrics.winRatePct.toFixed(1)}%` : "—"
                    )}
                  />
                  <MetricRow
                    label="Total return"
                    values={compareVersions.map((v) => fmtMoney(v.metrics.totalReturn))}
                  />
                  <MetricRow
                    label="Profit factor"
                    values={compareVersions.map((v) =>
                      v.metrics.profitFactor != null ? v.metrics.profitFactor.toFixed(2) : "—"
                    )}
                  />
                  <MetricRow
                    label="Expectancy"
                    values={compareVersions.map((v) => fmtMoney(v.metrics.expectancy))}
                  />
                  <MetricRow
                    label="Average win"
                    values={compareVersions.map((v) => fmtMoney(v.metrics.avgWin))}
                  />
                  <MetricRow
                    label="Average loss"
                    values={compareVersions.map((v) => fmtMoney(v.metrics.avgLoss))}
                  />
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-xl border border-bg-border bg-bg-panel p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-white">Deploy a new strategy version</h2>
              <button onClick={() => setShowCreate(false)} aria-label="Close">
                <X size={18} className="text-muted" />
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-xs text-muted">Version label</label>
                <input
                  value={newVersion}
                  onChange={(e) => setNewVersion(e.target.value)}
                  placeholder="v2"
                  className="w-full rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-sm text-white"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted">Description / what changed</label>
                <textarea
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  rows={3}
                  placeholder="e.g. Raised STRATEGY_BUY_THRESHOLD from 8.0 to 8.8 per approved recommendation #4"
                  className="w-full rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-sm text-white"
                />
              </div>
              {createError && <div className="text-xs text-loss">{createError}</div>}
              <p className="text-xs text-muted">
                This deactivates the current version and makes this the new active one - every trade
                entered from the bot's next cycle onward is tagged with this version. It does not
                change any environment variables or code; you still need to make the actual
                config/threshold change yourself (e.g. in Railway) to match what you describe here.
              </p>
              <button
                onClick={createVersion}
                disabled={creating || !newVersion}
                className="w-full rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {creating ? "Deploying…" : "Deploy version"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
