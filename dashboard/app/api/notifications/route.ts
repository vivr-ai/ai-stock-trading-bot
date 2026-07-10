import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";

export const dynamic = "force-dynamic";

type NotificationRow = {
  id: number;
  ts: string;
  type: string;
  severity: string;
  title: string;
  message: string | null;
};

export async function GET(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(req.url);
  const search = searchParams.get("search")?.trim() || "";
  const type = searchParams.get("type") || "all";
  const severity = searchParams.get("severity") || "all";
  const limit = Math.min(Number(searchParams.get("limit")) || 100, 500);

  const where: string[] = [];
  const params: unknown[] = [];

  if (search) {
    params.push(`%${search}%`);
    const idx = params.length;
    where.push(`(title ILIKE $${idx} OR message ILIKE $${idx})`);
  }
  if (type !== "all") {
    params.push(type);
    where.push(`type = $${params.length}`);
  }
  if (severity !== "all") {
    params.push(severity);
    where.push(`severity = $${params.length}`);
  }

  const whereClause = where.length ? `WHERE ${where.join(" AND ")}` : "";
  params.push(limit);

  try {
    const [notifications, heartbeat, counts] = await Promise.all([
      query<NotificationRow>(
        `SELECT id, ts, type, severity, title, message
         FROM notifications
         ${whereClause}
         ORDER BY ts DESC
         LIMIT $${params.length}`,
        params
      ),
      queryOne<{ ts: string }>("SELECT ts FROM heartbeats ORDER BY ts DESC LIMIT 1"),
      query<{ severity: string; count: string }>(
        "SELECT severity, count(*) FROM notifications GROUP BY severity"
      ),
    ]);

    const severityCounts: Record<string, number> = { critical: 0, warning: 0, info: 0 };
    for (const c of counts) {
      severityCounts[c.severity] = Number(c.count);
    }

    return NextResponse.json({
      notifications,
      hasEverRun: Boolean(heartbeat) || notifications.length > 0,
      severityCounts,
    });
  } catch (err) {
    console.error("GET /api/notifications failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
