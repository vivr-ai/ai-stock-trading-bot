import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";

export const dynamic = "force-dynamic";

type BotControlRow = {
  is_paused: boolean;
  reason: string | null;
  updated_at: string;
  updated_by: string | null;
};

// GET: current pause state. Read by the Home page's status strip and Quick
// Actions panel. The bot itself does NOT call this route - it reads the
// same bot_control row directly from Postgres (see bot/bot_control.py),
// same pattern as how it reads the active strategy version.
export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const row = await queryOne<BotControlRow>(
      "SELECT is_paused, reason, updated_at, updated_by FROM bot_control WHERE id = 1"
    );
    return NextResponse.json({
      isPaused: row?.is_paused ?? false,
      reason: row?.reason ?? null,
      updatedAt: row?.updated_at ?? null,
      updatedBy: row?.updated_by ?? null,
    });
  } catch (err) {
    console.error("GET /api/bot-control failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}

// POST: pause or resume new trading activity. This is the ONLY way
// bot_control.is_paused changes - always a deliberate, explicit action
// taken here by a signed-in human, never automatic and never triggered by
// any part of the analytics/research layer. The bot only ever reads this
// row (bot/bot_control.py's BotControlProvider), on up to a 60s cache, so
// a pause/resume set here takes effect on the bot's next cycle at worst.
//
// "Emergency Stop" from the dashboard calls this same endpoint with
// action: "pause" and a distinct reason string - it does NOT liquidate
// open positions. Pausing only blocks NEW entries; existing positions
// keep being managed by sentiment-driven sells and broker-side
// stop-loss/take-profit brackets (see bot/trading/strategy.py's
// new_entries_allowed gate). Liquidating positions is out of scope here
// and reserved for a future dedicated Kill Switch feature.
export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const body = await req.json();
    const { action, reason } = body as { action?: string; reason?: string };

    if (action !== "pause" && action !== "resume") {
      return NextResponse.json(
        { error: "action must be 'pause' or 'resume'" },
        { status: 400 }
      );
    }

    const isPaused = action === "pause";
    const updatedBy = session.user?.email ?? null;

    await query(
      `INSERT INTO bot_control (id, is_paused, reason, updated_at, updated_by)
       VALUES (1, $1, $2, now(), $3)
       ON CONFLICT (id) DO UPDATE SET
         is_paused = EXCLUDED.is_paused,
         reason = EXCLUDED.reason,
         updated_at = EXCLUDED.updated_at,
         updated_by = EXCLUDED.updated_by`,
      [isPaused, isPaused ? reason ?? "Paused from the dashboard." : null, updatedBy]
    );

    // Best-effort audit trail row in `notifications`. Note this does NOT
    // send a Telegram alert by itself - only the Python bot's
    // NotificationService does that (see bot/notifications/service.py).
    // The bot's own pause-state check in run_cycle() sends the real-time
    // Telegram notification once it next sees the state change; this row
    // just means the dashboard's Notifications feed reflects the action
    // immediately, even before the bot's next cycle.
    await query(
      `INSERT INTO notifications (type, severity, title, message)
       VALUES ($1, $2, $3, $4)`,
      [
        isPaused ? "bot_paused" : "bot_resumed",
        isPaused ? "warning" : "info",
        isPaused ? "Trading paused" : "Trading resumed",
        isPaused
          ? `Paused from the dashboard${updatedBy ? ` by ${updatedBy}` : ""}${
              reason ? `: ${reason}` : ""
            }. New entries are blocked; existing positions keep being managed.`
          : `Resumed from the dashboard${updatedBy ? ` by ${updatedBy}` : ""}. New entries are allowed again.`,
      ]
    );

    return NextResponse.json({ ok: true, isPaused });
  } catch (err) {
    console.error("POST /api/bot-control failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
