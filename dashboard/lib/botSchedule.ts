// When the bot is EXPECTED to report. The bot's scheduler only runs on
// weekdays 09:00-16:00 America/New_York (cycles at :00 and :30; see
// bot/scheduler.py), so outside that window no heartbeat is written and a
// 12-hour-old heartbeat on a Saturday is normal, not a stopped bot.
// Without this, every evening and weekend showed "Bot stopped".

const NY = "America/New_York";
export const STALE_HEARTBEAT_MINUTES = 45;

function nyParts(d: Date): { weekday: number; minutes: number } {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: NY, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  const parts = Object.fromEntries(fmt.formatToParts(d).map((p) => [p.type, p.value]));
  const weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(parts.weekday as string);
  return { weekday, minutes: Number(parts.hour) * 60 + Number(parts.minute) };
}

/** True while the scheduler should be running cycles (weekday 09:00-16:05 NY). */
export function inSchedulerWindow(d: Date): boolean {
  const { weekday, minutes } = nyParts(d);
  return weekday >= 1 && weekday <= 5 && minutes >= 9 * 60 && minutes <= 16 * 60 + 5;
}

/** Start of the most recent scheduler window that has already ended or is
 *  under way, walking back day by day (weekends skipped). */
function lastWindowEnd(now: Date): Date {
  // Walk back in 5-minute steps (at most ~4 days) until we're inside a window.
  const step = 5 * 60_000;
  for (let t = now.getTime(); t > now.getTime() - 5 * 86_400_000; t -= step) {
    if (inSchedulerWindow(new Date(t))) return new Date(t);
  }
  return new Date(now.getTime() - 5 * 86_400_000);
}

export type HeartbeatState = "running" | "idle" | "stopped";

/**
 * running  - inside the trading window and a heartbeat arrived recently
 * idle     - outside the window, and the bot reported normally until the end
 *            of its last window (the expected evening/weekend state)
 * stopped  - a heartbeat is overdue: inside the window and nothing for 45
 *            min, or outside it and the last window ended without reports
 */
export function heartbeatState(lastHeartbeat: string | Date | null, now: Date = new Date()): HeartbeatState {
  if (!lastHeartbeat) return "stopped";
  const last = new Date(lastHeartbeat);
  const ageMin = (now.getTime() - last.getTime()) / 60000;
  if (inSchedulerWindow(now)) {
    return ageMin <= STALE_HEARTBEAT_MINUTES ? "running" : "stopped";
  }
  const windowEnd = lastWindowEnd(now);
  const gapMin = (windowEnd.getTime() - last.getTime()) / 60000;
  return gapMin <= STALE_HEARTBEAT_MINUTES ? "idle" : "stopped";
}

/** The New York calendar date (YYYY-MM-DD) of a timestamp - the trading
 *  "day" a snapshot belongs to. */
export function nyDate(ts: string | Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: NY, year: "numeric", month: "2-digit", day: "2-digit" })
    .format(new Date(ts));
}
