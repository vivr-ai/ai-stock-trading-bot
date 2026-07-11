import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { query } from "@/lib/db";

export const dynamic = "force-dynamic";

type SettingRow = {
  type: string;
  channel: string;
  enabled: boolean;
  label: string | null;
  description: string | null;
  updated_at: string;
};

const VALID_CHANNELS = new Set(["immediate", "daily_summary", "weekly_summary", "off"]);

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const rows = await query<SettingRow>(
      `SELECT type, channel, enabled, label, description, updated_at
       FROM notification_settings ORDER BY type ASC`
    );
    return NextResponse.json({ settings: rows });
  } catch (err) {
    console.error("GET /api/notification-settings failed", err);
    // Most likely cause: the bot service hasn't redeployed yet since this
    // phase added the notification_settings table (it's applied
    // automatically by scripts/apply_schema.py via releaseCommand - see
    // NOTIFICATIONS.md).
    return NextResponse.json(
      {
        error:
          (err instanceof Error ? err.message : "Unknown error") +
          " — has the bot service redeployed since this update? It applies " +
          "db/schema.sql automatically on release.",
      },
      { status: 500 }
    );
  }
}

export async function PUT(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: { type?: string; channel?: string; enabled?: boolean };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { type, channel, enabled } = body;
  if (!type || typeof type !== "string") {
    return NextResponse.json({ error: "type is required" }, { status: 400 });
  }
  if (channel !== undefined && !VALID_CHANNELS.has(channel)) {
    return NextResponse.json(
      { error: `channel must be one of: ${Array.from(VALID_CHANNELS).join(", ")}` },
      { status: 400 }
    );
  }
  if (enabled !== undefined && typeof enabled !== "boolean") {
    return NextResponse.json({ error: "enabled must be a boolean" }, { status: 400 });
  }

  try {
    const sets: string[] = [];
    const params: unknown[] = [];
    if (channel !== undefined) {
      params.push(channel);
      sets.push(`channel = $${params.length}`);
    }
    if (enabled !== undefined) {
      params.push(enabled);
      sets.push(`enabled = $${params.length}`);
    }
    sets.push("updated_at = now()");
    params.push(type);

    const rows = await query<SettingRow>(
      `UPDATE notification_settings SET ${sets.join(", ")}
       WHERE type = $${params.length}
       RETURNING type, channel, enabled, label, description, updated_at`,
      params
    );
    if (rows.length === 0) {
      return NextResponse.json({ error: `Unknown notification type: ${type}` }, { status: 404 });
    }
    return NextResponse.json({ setting: rows[0] });
  } catch (err) {
    console.error("PUT /api/notification-settings failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
