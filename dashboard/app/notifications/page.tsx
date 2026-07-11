"use client";

import { useEffect, useMemo, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import {
  Search,
  TrendingUp,
  XCircle,
  AlertTriangle,
  RotateCw,
  FileText,
  CalendarDays,
  Bell,
  PowerOff,
  Rocket,
  ShieldAlert,
  DatabaseZap,
  TimerReset,
} from "lucide-react";

type NotificationRow = {
  id: number;
  ts: string;
  type: string;
  severity: string;
  title: string;
  message: string | null;
};

type NotificationsResponse = {
  notifications: NotificationRow[];
  hasEverRun: boolean;
  severityCounts: Record<string, number>;
};

const TYPE_META: Record<string, { icon: typeof Bell; label: string }> = {
  trade_executed: { icon: TrendingUp, label: "Trade" },
  error: { icon: XCircle, label: "Error" },
  broker_issue: { icon: AlertTriangle, label: "Broker issue" },
  bot_restart: { icon: RotateCw, label: "Bot restart" },
  bot_stopped_unexpectedly: { icon: PowerOff, label: "Bot stopped unexpectedly" },
  deployment_completed: { icon: Rocket, label: "Deployment completed" },
  daily_summary: { icon: FileText, label: "Daily summary" },
  weekly_summary: { icon: CalendarDays, label: "Weekly summary" },
  daily_loss_limit: { icon: ShieldAlert, label: "Daily loss limit" },
  database_failure: { icon: DatabaseZap, label: "Database failure" },
  scheduler_failure: { icon: TimerReset, label: "Scheduler failure" },
};

const SEVERITY_CLASS: Record<string, string> = {
  critical: "border-loss/40 bg-loss/10 text-loss",
  warning: "border-accent/40 bg-accent/10 text-accent",
  info: "border-bg-border bg-bg-panel2 text-muted",
};

function NotificationCard({ n }: { n: NotificationRow }) {
  const meta = TYPE_META[n.type] ?? { icon: Bell, label: n.type };
  const Icon = meta.icon;
  const sevClass = SEVERITY_CLASS[n.severity] ?? SEVERITY_CLASS.info;

  return (
    <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border ${sevClass}`}>
            <Icon size={14} />
          </span>
          <span className="font-medium text-white">{n.title}</span>
          <span className="rounded-full bg-bg-panel2 px-2 py-0.5 text-xs text-muted">{meta.label}</span>
        </div>
        <span className="text-xs text-muted">
          {new Date(n.ts).toLocaleString(undefined, {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
      </div>
      {n.message && (
        <p className="mt-2 whitespace-pre-line text-sm text-muted">{n.message}</p>
      )}
    </div>
  );
}

export default function NotificationsPage() {
  const [data, setData] = useState<NotificationsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState("");
  const [type, setType] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [limit, setLimit] = useState(100);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (type !== "all") params.set("type", type);
    if (severity !== "all") params.set("severity", severity);
    params.set("limit", String(limit));
    return params.toString();
  }, [search, type, severity, limit]);

  async function load() {
    try {
      const res = await fetch(`/api/notifications?${queryString}`);
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
    const id = setTimeout(load, 250);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryString]);

  useEffect(() => {
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Notifications Centre</h1>
        <p className="text-sm text-muted">Trades, errors, broker issues, restarts, and daily/weekly summaries.</p>
      </div>

      {!loading && !error && data && (
        <div className="mb-4 grid grid-cols-3 gap-3">
          <div className="rounded-xl border border-loss/40 bg-loss/10 p-3 text-center">
            <div className="text-lg font-semibold text-loss">{data.severityCounts.critical ?? 0}</div>
            <div className="text-xs text-loss/80">Critical</div>
          </div>
          <div className="rounded-xl border border-accent/40 bg-accent/10 p-3 text-center">
            <div className="text-lg font-semibold text-accent">{data.severityCounts.warning ?? 0}</div>
            <div className="text-xs text-accent/80">Warning</div>
          </div>
          <div className="rounded-xl border border-bg-border bg-bg-panel2 p-3 text-center">
            <div className="text-lg font-semibold text-white">{data.severityCounts.info ?? 0}</div>
            <div className="text-xs text-muted">Info</div>
          </div>
        </div>
      )}

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search title or message..."
            className="w-full rounded-lg border border-bg-border bg-bg-panel2 py-2 pl-9 pr-3 text-sm text-white outline-none focus:border-accent"
          />
        </div>
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-sm text-white outline-none focus:border-accent"
        >
          <option value="all">All types</option>
          <option value="trade_executed">Trades</option>
          <option value="error">Errors</option>
          <option value="broker_issue">Broker issues</option>
          <option value="bot_restart">Bot restarts</option>
          <option value="bot_stopped_unexpectedly">Bot stopped unexpectedly</option>
          <option value="deployment_completed">Deployments</option>
          <option value="daily_summary">Daily summaries</option>
          <option value="weekly_summary">Weekly summaries</option>
          <option value="daily_loss_limit">Daily loss limit</option>
          <option value="database_failure">Database failures</option>
          <option value="scheduler_failure">Scheduler failures</option>
        </select>
        <select
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
          className="rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-sm text-white outline-none focus:border-accent"
        >
          <option value="all">All severities</option>
          <option value="critical">Critical</option>
          <option value="warning">Warning</option>
          <option value="info">Info</option>
        </select>
      </div>

      {loading && <LoadingSkeleton rows={4} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && !data.hasEverRun && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No data yet. This fills in once the bot has run at least one cycle.
        </div>
      )}

      {!loading && !error && data && data.hasEverRun && data.notifications.length === 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No notifications match these filters yet.
        </div>
      )}

      {!loading && !error && data && data.notifications.length > 0 && (
        <div className="space-y-3">
          {data.notifications.map((n) => (
            <NotificationCard key={n.id} n={n} />
          ))}
          {data.notifications.length >= limit && (
            <button
              onClick={() => setLimit((l) => l + 100)}
              className="w-full rounded-lg border border-bg-border bg-bg-panel py-2 text-sm text-muted hover:text-white"
            >
              Load more
            </button>
          )}
        </div>
      )}
    </div>
  );
}
