"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";

type Setting = {
  type: string;
  channel: string;
  enabled: boolean;
  label: string | null;
  description: string | null;
  updated_at: string;
};

const CHANNEL_OPTIONS: { value: string; label: string }[] = [
  { value: "immediate", label: "Send immediately" },
  { value: "daily_summary", label: "Daily summary only" },
  { value: "weekly_summary", label: "Weekly summary only" },
  { value: "off", label: "Off (Telegram muted)" },
];

export default function NotificationSettingsPage() {
  const [settings, setSettings] = useState<Setting[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingType, setSavingType] = useState<string | null>(null);

  async function load() {
    try {
      const res = await fetch("/api/notification-settings");
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Request failed");
      setSettings(json.settings);
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

  async function update(type: string, patch: Partial<Pick<Setting, "channel" | "enabled">>) {
    setSavingType(type);
    // Optimistic update.
    setSettings((prev) =>
      prev ? prev.map((s) => (s.type === type ? { ...s, ...patch } : s)) : prev
    );
    try {
      const res = await fetch("/api/notification-settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type, ...patch }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Save failed");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
      load(); // revert to server state
    } finally {
      setSavingType(null);
    }
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Notification Settings</h1>
        <p className="text-sm text-muted">
          Choose which alerts go straight to Telegram, which only show up in the daily/weekly
          summary, and which are muted. Everything is always kept in the Notifications Centre
          regardless of these settings. Changes take effect on the bot&apos;s next cycle
          (~1 minute), no redeploy needed.
        </p>
      </div>

      {loading && <LoadingSkeleton rows={4} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && settings && (
        <div className="space-y-3">
          {settings.map((s) => (
            <div
              key={s.type}
              className="flex flex-col gap-3 rounded-xl border border-bg-border bg-bg-panel p-4 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-white">{s.label ?? s.type}</span>
                  {!s.enabled && (
                    <span className="rounded-full bg-bg-panel2 px-2 py-0.5 text-xs text-muted">
                      Muted
                    </span>
                  )}
                </div>
                {s.description && (
                  <p className="mt-1 text-sm text-muted">{s.description}</p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <select
                  value={s.channel}
                  disabled={savingType === s.type}
                  onChange={(e) => update(s.type, { channel: e.target.value, enabled: true })}
                  className="rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-sm text-white outline-none focus:border-accent disabled:opacity-50"
                >
                  {CHANNEL_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                <label className="flex items-center gap-2 text-sm text-muted">
                  <input
                    type="checkbox"
                    checked={s.enabled}
                    disabled={savingType === s.type}
                    onChange={(e) => update(s.type, { enabled: e.target.checked })}
                    className="h-4 w-4 rounded border-bg-border accent-accent"
                  />
                  Enabled
                </label>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
