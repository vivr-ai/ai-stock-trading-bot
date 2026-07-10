import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";

export const dynamic = "force-dynamic";

type ClosedTrade = {
  id: number;
  ts: string; // sell time
  symbol: string;
  qty: number;
  entry_price: number;
  exit_price: number;
  pnl: number;
  pnl_pct: number;
  exit_reason: string | null;
  entry_time: string | null; // buy time
  buy_reason: string | null;
  news_summary: string | null;
};

export async function GET(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(req.url);
  const search = searchParams.get("search")?.trim() || "";
  const outcome = searchParams.get("outcome") || "all"; // all | win | loss
  const from = searchParams.get("from") || "";
  const to = searchParams.get("to") || "";
  const limit = Math.min(Number(searchParams.get("limit")) || 200, 500);

  const where: string[] = [];
  const params: unknown[] = [];

  if (search) {
    params.push(`%${search}%`);
    const idx = params.length;
    where.push(
      `(symbol ILIKE $${idx} OR buy_reason ILIKE $${idx} OR news_summary ILIKE $${idx} OR exit_reason ILIKE $${idx})`
    );
  }
  if (outcome === "win") {
    where.push("pnl > 0");
  } else if (outcome === "loss") {
    where.push("pnl < 0");
  }
  if (from) {
    params.push(from);
    where.push(`ts >= $${params.length}`);
  }
  if (to) {
    params.push(to);
    where.push(`ts <= $${params.length}`);
  }

  const whereClause = where.length ? `WHERE ${where.join(" AND ")}` : "";
  params.push(limit);

  try {
    const [trades, heartbeat] = await Promise.all([
      query<ClosedTrade>(
        `SELECT id, ts, symbol, qty, entry_price, exit_price, pnl, pnl_pct,
                exit_reason, entry_time, buy_reason, news_summary
         FROM closed_trades
         ${whereClause}
         ORDER BY ts DESC
         LIMIT $${params.length}`,
        params
      ),
      queryOne<{ ts: string }>("SELECT ts FROM heartbeats ORDER BY ts DESC LIMIT 1"),
    ]);

    return NextResponse.json({ trades, hasEverRun: Boolean(heartbeat) });
  } catch (err) {
    console.error("GET /api/trades failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
