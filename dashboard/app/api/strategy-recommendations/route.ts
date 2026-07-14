import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";

export const dynamic = "force-dynamic";

export type RecommendationRow = {
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
  proposed_config_changes: Record<string, unknown> | null;
  status: "pending" | "approved" | "rejected";
  reviewed_at: string | null;
  reviewed_by: string | null;
  review_notes: string | null;
  backtest_result: Record<string, unknown> | null;
  deployed_as_version: string | null;
};

// GET: every recommendation, newest first. Pattern Discovery (Phase 3) and
// the AI Research Assistant (Phase 4) INSERT rows here with source set
// accordingly; this route also accepts manual entries (source='manual') so
// the approval workflow can be exercised end-to-end before those phases
// exist.
export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const rows = await query<RecommendationRow>(
      `SELECT id, created_at, source, title, observation, evidence, statistical_confidence,
              estimated_impact, risks, recommendation, priority, proposed_config_changes,
              status, reviewed_at, reviewed_by, review_notes, backtest_result, deployed_as_version
       FROM strategy_recommendations ORDER BY created_at DESC LIMIT 500`
    );
    return NextResponse.json({ recommendations: rows });
  } catch (err) {
    console.error("GET /api/strategy-recommendations failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}

// POST: create a recommendation manually. Real automated sources (Phase 3/4)
// write directly to Postgres rather than going through this HTTP route, but
// the shape is identical either way.
export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const body = await req.json();
    const {
      title, observation, evidence, statisticalConfidence, estimatedImpact,
      risks, recommendation, priority, proposedConfigChanges,
    } = body as Record<string, unknown>;

    if (!title || typeof title !== "string") {
      return NextResponse.json({ error: "title is required" }, { status: 400 });
    }

    const row = await queryOne<{ id: number }>(
      `INSERT INTO strategy_recommendations
        (source, title, observation, evidence, statistical_confidence, estimated_impact,
         risks, recommendation, priority, proposed_config_changes)
       VALUES ('manual', $1, $2, $3, $4, $5, $6, $7, $8, $9)
       RETURNING id`,
      [
        title, observation ?? null, evidence ?? null, statisticalConfidence ?? null,
        estimatedImpact ?? null, risks ?? null, recommendation ?? null,
        (priority as string) ?? "medium",
        proposedConfigChanges ? JSON.stringify(proposedConfigChanges) : null,
      ]
    );

    return NextResponse.json({ ok: true, id: row?.id });
  } catch (err) {
    console.error("POST /api/strategy-recommendations failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}

// PATCH: approve or reject a recommendation. This ONLY updates status/review
// fields on the recommendation itself - it never touches strategy_versions
// or any trading behaviour. Turning an approved recommendation into an
// active strategy version is a separate, explicit action on the Strategy
// Versions page (POST /api/strategy-versions).
export async function PATCH(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const body = await req.json();
    const { id, status, reviewNotes } = body as {
      id?: number; status?: string; reviewNotes?: string;
    };
    if (!id || !status || !["approved", "rejected", "pending"].includes(status)) {
      return NextResponse.json({ error: "id and a valid status are required" }, { status: 400 });
    }
    await query(
      `UPDATE strategy_recommendations
       SET status = $1, reviewed_at = now(), reviewed_by = $2, review_notes = $3
       WHERE id = $4`,
      [status, session.user?.email ?? session.user?.name ?? "dashboard user", reviewNotes ?? null, id]
    );
    return NextResponse.json({ ok: true });
  } catch (err) {
    console.error("PATCH /api/strategy-recommendations failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
