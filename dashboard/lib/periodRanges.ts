// Preset ranges for the Performance page's period selector (Yahoo Finance-
// style: 1W/1M/3M/6M/YTD/1Y/FY/All). The rolling windows (1W/1M/3M/6M/1Y)
// use the browser's own clock - a "month ago" instant doesn't depend on
// timezone to within a few hours either way - but "FY" and "YTD" delegate
// to lib/financialYear.ts, the same Brisbane-anchored (fixed UTC+10, no
// DST) source of truth the server-side Accountant Export route uses. That
// reconciliation matters: this file used to compute FY from the *viewer's*
// local calendar and financialYear.ts computed it from the *server's* raw
// UTC clock, so the two could disagree by hours around a boundary and
// there were two definitions of "what FY is it" to keep in sync by hand.
import { fyStartDate, calendarYtdStartDate } from "./financialYear";

export type RangeKey = "1w" | "1m" | "3m" | "6m" | "ytd" | "1y" | "fy" | "all";

export const RANGE_OPTIONS: { key: RangeKey; label: string }[] = [
  { key: "1w", label: "1W" },
  { key: "1m", label: "1M" },
  { key: "3m", label: "3M" },
  { key: "6m", label: "6M" },
  { key: "ytd", label: "YTD" },
  { key: "1y", label: "1Y" },
  { key: "fy", label: "FY" },
  { key: "all", label: "All" },
];

/** Start of the selected range, or null for "all" (no lower bound - the
 * original, unfiltered behavior). Australian financial year (1 Jul - 30
 * Jun) for "fy", matching the ATO tax year rather than the US FY or
 * calendar year. */
export function rangeStart(key: RangeKey, now: Date = new Date()): Date | null {
  switch (key) {
    case "1w": {
      const d = new Date(now);
      d.setDate(d.getDate() - 7);
      return d;
    }
    case "1m": {
      const d = new Date(now);
      d.setMonth(d.getMonth() - 1);
      return d;
    }
    case "3m": {
      const d = new Date(now);
      d.setMonth(d.getMonth() - 3);
      return d;
    }
    case "6m": {
      const d = new Date(now);
      d.setMonth(d.getMonth() - 6);
      return d;
    }
    case "ytd":
      return calendarYtdStartDate(now);
    case "1y": {
      const d = new Date(now);
      d.setFullYear(d.getFullYear() - 1);
      return d;
    }
    case "fy":
      return fyStartDate(now);
    case "all":
    default:
      return null;
  }
}

/** Human label for what's currently selected, e.g. for an empty-state
 * message ("No trades in the last 3M") - kept in one place so it can't
 * drift from RANGE_OPTIONS' button labels. */
export function rangeLabel(key: RangeKey): string {
  return RANGE_OPTIONS.find((o) => o.key === key)?.label ?? "All";
}
