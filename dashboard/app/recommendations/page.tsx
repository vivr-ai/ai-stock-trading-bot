"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { timeAgo } from "@/lib/format";
import { CheckCircle2, XCircle, Sparkles } from "lucide-react";

type Recommendation = {
  id: number;
  created_at: string;
  source: string;
  title: string;
  observation: string | null;
  evidence: string | null;
  statistical_confidence: string | null;
  estimated_impact: string | null;
  risks: string | null;
  recommendation: string | null;
  priority: "low" | "medium" | "high";
  status: "pending" | "approved" | "rejected";
  reviewed_at: string | null;
  reviewed_by: string | null;
  review_notes: string | null;
  deployed_as_version: string | null;
};

const PRIORITY_STYLE: Record<string, string> = {
  high: "bg-loss/15 text-loss",
  medium: "bg-accent/15 text-accent",
  low: "bg-bg-panel2 text-muted",
};

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-bg-panel2 text-muted",
  approved: "bg-gain/15 text-gain",
  rejected: "bg-loss/15 text-loss",
};

function RecCard({
  rec,
  onReview,
}: {
  rec: Recommendation;
  onReview: (id: number, status: "approved" | "rejected", notes: string) => void;
}) {
  const [notes, setNotes] = useState("");
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${PRIORITY_STYLE[rec.priority]}`}>
          {rec.priority} priority
        </span>
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[rec.status]}`}>
          {rec.status}
        </span>
        <span className="text-xs text-muted">{rec.source.replace("_", " ")}</span>
        <span className="text-xs text-muted">· {timeAgo(rec.created_at)}</span>
        {rec.deployed_as_version && (
          <span className="rounded-full bg-accent/15 px-2 py-0.5 text-xs text-accent">
            Deployed as {rec.deployed_as_version}
          </span>
        )}
      </div>
      <h3 className="mb-2 text-sm font-semibold text-white">{rec.title}</h3>

      <button onClick={() => setExpanded((e) => !e)} className="mb-2 text-xs text-accent">
        {expanded ? "Hide details" : "Show details"}
      </button>

      {expanded && (
        <div className="mb-3 space-y-2 text-xs text-muted">
          {rec.observation && <div><span className="text-white">Observation: </span>{rec.observation}</div>}
          {rec.evidence && <div><span className="text-white">Evidence: </span>{rec.evidence}</div>}
          {rec.statistical_confidence && (
            <div><span className="text-white">Statistical confidence: </span>{rec.statistical_confidence}</div>
          )}
          {rec.estimated_impact && (
            <div><span className="text-white">Estimated impact: </span>{rec.estimated_impact}</div>
          )}
          {rec.risks && <div><span className="text-white">Risks: </span>{rec.risks}</div>}
          {rec.recommendation && (
            <div><span className="text-white">Recommendation: </span>{rec.recommendation}</div>
          )}
          {rec.review_notes && (
            <div className="border-t border-bg-border pt-2">
              <span className="text-white">Review notes: </span>{rec.review_notes}
              {rec.reviewed_by && ` — ${rec.reviewed_by}`}
              {rec.reviewed_at && ` (${timeAgo(rec.reviewed_at)})`}
            </div>
          )}
        </div>
      )}

      {rec.status === "pending" && (
        <div className="space-y-2 border-t border-bg-border pt-3">
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Optional review notes"
            rows={2}
            className="w-full rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-xs text-white"
          />
          <div className="flex gap-2">
            <button
              onClick={() => onReview(rec.id, "approved", notes)}
              className="flex items-center gap-1.5 rounded-lg bg-gain/15 px-3 py-1.5 text-xs font-medium text-gain hover:bg-gain/25"
            >
              <CheckCircle2 size={14} /> Approve
            </button>
            <button
              onClick={() => onReview(rec.id, "rejected", notes)}
              className="flex items-center gap-1.5 rounded-lg bg-loss/15 px-3 py-1.5 text-xs font-medium text-loss hover:bg-loss/25"
            >
              <XCircle size={14} /> Reject
            </button>
          </div>
        </div>
      )}

      {rec.status === "approved" && !rec.deployed_as_version && (
        <div className="mt-2 rounded-lg border border-dashed border-bg-border p-2 text-xs text-muted">
          Approved. To actually change trading behaviour, go to Strategy Versions and deploy a new
          version referencing this recommendation - approval alone changes nothing.
        </div>
      )}
    </div>
  );
}

export default function RecommendationsPage() {
  const [recs, setRecs] = useState<Recommendation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"pending" | "approved" | "rejected" | "all">("pending");

  async function load() {
    try {
      const res = await fetch("/api/strategy-recommendations");
      if (!res.ok) throw new Error((await res.json()).error || "Request failed");
      const json = await res.json();
      setRecs(json.recommendations);
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

  async function onReview(id: number, status: "approved" | "rejected", notes: string) {
    await fetch("/api/strategy-recommendations", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, status, reviewNotes: notes }),
    });
    await load();
  }

  const filtered = recs?.filter((r) => filter === "all" || r.status === filter) ?? [];

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Recommendations</h1>
        <p className="text-sm text-muted">
          Advisory only - approving a recommendation here never changes trading behaviour by itself.
          Pattern Discovery and the AI Research Assistant (later phases) will populate this list
          automatically; for now it's empty until those phases exist or you add one manually via the API.
        </p>
      </div>

      <div className="mb-4 flex gap-2 border-b border-bg-border">
        {(["pending", "approved", "rejected", "all"] as const).map((key) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`px-3 py-2 text-sm font-medium capitalize transition-colors ${
              filter === key ? "border-b-2 border-accent text-white" : "text-muted hover:text-white"
            }`}
          >
            {key}
          </button>
        ))}
      </div>

      {loading && <LoadingSkeleton rows={3} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && filtered.length === 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-center text-sm text-muted">
          <Sparkles size={20} className="mx-auto mb-2 text-muted" />
          No {filter !== "all" ? filter : ""} recommendations yet.
        </div>
      )}

      {!loading && !error && filtered.length > 0 && (
        <div className="space-y-4">
          {filtered.map((rec) => (
            <RecCard key={rec.id} rec={rec} onReview={onReview} />
          ))}
        </div>
      )}
    </div>
  );
}
