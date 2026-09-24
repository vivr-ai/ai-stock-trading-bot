/** Australian financial year (and calendar-year) helpers: FY = 1 July
 * (startYear) - 30 June (startYear+1). Single source of truth for every
 * FY/YTD boundary in the dashboard - both the server-side Accountant
 * Export route and the client-side Performance page's period selector
 * import from here, so "what FY is it" can never quietly disagree between
 * the two.
 *
 * Queensland observes no daylight saving, so Brisbane is a fixed UTC+10
 * year-round - there's no DST transition to get wrong here, just a
 * constant offset. brisbaneNow() converts any instant to its Brisbane
 * calendar date via that fixed offset, so the boundary comes out the same
 * wherever this code runs: a Node server (Railway's containers run UTC,
 * not Brisbane time) or a browser (wherever the viewer happens to be -
 * this is a single-user Brisbane app, so that shouldn't matter in
 * practice, but the tax year shouldn't silently shift if it ever does).
 */

/** The Brisbane-local wall-clock time of `now`, as a Date whose UTC
 * getters (getUTCFullYear/getUTCMonth/getUTCDate/...) read as if they were
 * Brisbane-local getters. Do not call getTime() on the result and treat it
 * as a real instant - only its UTC calendar fields are meaningful. */
export function brisbaneNow(now: Date = new Date()): Date {
  return new Date(now.getTime() + 10 * 60 * 60 * 1000);
}

/** The real UTC instant corresponding to Brisbane-local Y-M-D 00:00:00. */
function brisbaneMidnightUtc(year: number, monthIndex0: number, day: number): Date {
  return new Date(Date.UTC(year, monthIndex0, day) - 10 * 60 * 60 * 1000);
}

export function fyLabel(startYear: number): string {
  return `${startYear}-${startYear + 1}`;
}

export function fyDateRange(startYear: number): { start: string; end: string } {
  return { start: `${startYear}-07-01`, end: `${startYear + 1}-06-30` };
}

export function currentFyStartYear(now: Date = new Date()): number {
  const b = brisbaneNow(now);
  const y = b.getUTCFullYear();
  const m = b.getUTCMonth() + 1; // 1-12
  return m >= 7 ? y : y - 1;
}

/** The real UTC instant of 1 July 00:00 Brisbane time for the FY `now`
 * falls in - e.g. as a lower bound for "WHERE ts >= this" or a chart's
 * FY-range filter. */
export function fyStartDate(now: Date = new Date()): Date {
  return brisbaneMidnightUtc(currentFyStartYear(now), 6, 1);
}

/** The real UTC instant of 1 January 00:00 Brisbane time in the year
 * `now` falls in - e.g. for a "year to date" filter. */
export function calendarYtdStartDate(now: Date = new Date()): Date {
  const b = brisbaneNow(now);
  return brisbaneMidnightUtc(b.getUTCFullYear(), 0, 1);
}

export function isFyComplete(startYear: number, now: Date = new Date()): boolean {
  // The FY ending `startYear + 1` is complete once Brisbane's next FY has
  // started - reuses fyStartDate rather than a separate "T23:59:59Z"
  // cutoff, which (being raw UTC) used to call a FY complete up to 10
  // hours late relative to Brisbane's actual midnight.
  return now.getTime() >= brisbaneMidnightUtc(startYear + 1, 6, 1).getTime();
}

export function parseFyLabel(label: string): number | null {
  const m = /^(\d{4})-(\d{4})$/.exec(label);
  if (!m) return null;
  const startYear = Number(m[1]);
  if (Number(m[2]) !== startYear + 1) return null;
  return startYear;
}

/** Every FY label from the one containing `earliestDateIso` through the current FY, newest first. */
export function listAvailableFYs(earliestDateIso: string | null, now: Date = new Date()): string[] {
  const current = currentFyStartYear(now);
  const earliestStartYear = earliestDateIso ? currentFyStartYear(new Date(earliestDateIso)) : current;
  const years: string[] = [];
  for (let y = current; y >= earliestStartYear; y--) {
    years.push(fyLabel(y));
  }
  return years;
}
