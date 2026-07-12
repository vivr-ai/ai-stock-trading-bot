/** Australian financial year helpers: 1 July (startYear) - 30 June (startYear+1). */

export function fyLabel(startYear: number): string {
  return `${startYear}-${startYear + 1}`;
}

export function fyDateRange(startYear: number): { start: string; end: string } {
  return { start: `${startYear}-07-01`, end: `${startYear + 1}-06-30` };
}

export function currentFyStartYear(now: Date = new Date()): number {
  const y = now.getUTCFullYear();
  const m = now.getUTCMonth() + 1; // 1-12
  return m >= 7 ? y : y - 1;
}

export function isFyComplete(startYear: number, now: Date = new Date()): boolean {
  const { end } = fyDateRange(startYear);
  return now.getTime() > new Date(`${end}T23:59:59Z`).getTime();
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
  let earliestStartYear = current;
  if (earliestDateIso) {
    const d = new Date(earliestDateIso);
    const y = d.getUTCFullYear();
    const m = d.getUTCMonth() + 1;
    earliestStartYear = m >= 7 ? y : y - 1;
  }
  const years: string[] = [];
  for (let y = current; y >= earliestStartYear; y--) {
    years.push(fyLabel(y));
  }
  return years;
}
