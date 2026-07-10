import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";

export const dynamic = "force-dynamic";

type OpenPosition = {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  allocation_pct: number | null;
  ai_confidence: number | null;
  entry_reason: string | null;
  entry_time: string | null;
  updated_at: string;
};

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const [positions, heartbeat] = await Promise.all([
      query<OpenPosition>("SELECT * FROM open_positions ORDER BY market_value DESC NULLS LAST"),
      queryOne<{ ts: string }>("SELECT ts FROM heartbeats ORDER BY ts DESC LIMIT 1"),
    ]);

    return NextResponse.json({
      positions,
      hasEverRun: Boolean(heartbeat),
      updatedAt: positions[0]?.updated_at ?? null,
    });
  } catch (err) {
    console.error("GET /api/positions failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
