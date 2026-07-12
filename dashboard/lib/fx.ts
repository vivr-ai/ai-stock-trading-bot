import { query } from "@/lib/db";

/**
 * FX rate handling for the Accountant Export.
 *
 * Convention: every rate in this module and in the `fx_rates` table follows
 * the RBA's own quoting convention (their F11 table, Series ID "FXRUSD"):
 *   1 AUD = audUsdRate USD
 * To convert a USD amount to AUD: audAmount = usdAmount / audUsdRate.
 *
 * Design: `FxRateProvider` is a small interface so a different source (e.g.
 * a live FX API) can be added later without touching the export code that
 * consumes `getAudUsdRate` / `usdToAud`. RBA is the default and only
 * provider for now, per ATO-reporting preference. Rates are cached in
 * Postgres (`fx_rates`) the first time a date range is needed, so exports
 * stay reproducible even if RBA's site becomes unreachable later.
 */

export type FxRate = { date: string; audUsdRate: number };

export interface FxRateProvider {
  /** Fetch all available daily rates; the caller filters/caches what it needs. */
  fetchAll(): Promise<FxRate[]>;
  readonly sourceName: string;
}

const RBA_CSV_URL = "https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv";
const RBA_SERIES_ID = "FXRUSD"; // stable RBA series code for the daily AUD/USD rate

/**
 * Parses RBA's standard statistical-table CSV export format. These files
 * always have a metadata preamble (title/description/units/source/etc.)
 * followed by a "Series ID" row of machine-readable codes, then daily data
 * rows starting with a DD-Mon-YYYY date. This parser locates both
 * structurally (rather than assuming fixed row/column numbers), so it keeps
 * working even if RBA adds/reorders currency columns or preamble rows.
 */
function parseRbaCsv(csvText: string): FxRate[] {
  const rows = csvText
    .split(/\r?\n/)
    .map((line) => line.split(","))
    .filter((cols) => cols.length > 1);

  const seriesIdRowIdx = rows.findIndex((cols) => cols[0]?.trim() === "Series ID");
  if (seriesIdRowIdx === -1) {
    throw new Error("RBA CSV: could not find 'Series ID' row - file format may have changed.");
  }
  const usdColIdx = rows[seriesIdRowIdx].findIndex((c) => c.trim() === RBA_SERIES_ID);
  if (usdColIdx === -1) {
    throw new Error(`RBA CSV: could not find series '${RBA_SERIES_ID}' (AUD/USD) column.`);
  }

  const dateRe = /^\d{2}-[A-Za-z]{3}-\d{4}$/;
  const rates: FxRate[] = [];
  for (let i = seriesIdRowIdx + 1; i < rows.length; i++) {
    const cols = rows[i];
    const rawDate = cols[0]?.trim();
    if (!rawDate || !dateRe.test(rawDate)) continue; // still in preamble, or trailing blank row
    const raw = cols[usdColIdx]?.trim();
    if (!raw) continue; // no rate published that day (weekend/holiday)
    const rate = Number(raw);
    if (!Number.isFinite(rate) || rate <= 0) continue;
    rates.push({ date: rbaDateToIso(rawDate), audUsdRate: rate });
  }
  return rates;
}

function rbaDateToIso(rbaDate: string): string {
  // "04-Jan-2023" -> "2023-01-04"
  const months: Record<string, string> = {
    Jan: "01", Feb: "02", Mar: "03", Apr: "04", May: "05", Jun: "06",
    Jul: "07", Aug: "08", Sep: "09", Oct: "10", Nov: "11", Dec: "12",
  };
  const [day, mon, year] = rbaDate.split("-");
  return `${year}-${months[mon]}-${day.padStart(2, "0")}`;
}

export class RbaFxProvider implements FxRateProvider {
  readonly sourceName = "RBA";

  async fetchAll(): Promise<FxRate[]> {
    const res = await fetch(RBA_CSV_URL, { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`RBA CSV fetch failed: HTTP ${res.status}`);
    }
    const text = await res.text();
    return parseRbaCsv(text);
  }
}

const defaultProvider: FxRateProvider = new RbaFxProvider();

/**
 * Ensures fx_rates has coverage for [startDate, endDate] (both YYYY-MM-DD).
 * Fetches and upserts the provider's full series if any date in range is
 * missing from the cache. Cheap to call repeatedly - a no-op once cached.
 */
export async function ensureFxRatesCached(
  startDate: string,
  endDate: string,
  provider: FxRateProvider = defaultProvider
): Promise<void> {
  const gaps = await query<{ missing: boolean }>(
    `SELECT true as missing
     FROM generate_series($1::timestamp, $2::timestamp, interval '1 day') d
     LEFT JOIN fx_rates r ON r.rate_date = d::date
     WHERE r.rate_date IS NULL
       AND extract(dow from d) NOT IN (0, 6) -- ignore weekends, RBA doesn't publish then
     LIMIT 1`,
    [startDate, endDate]
  );
  if (gaps.length === 0) return; // fully cached already

  const rates = await provider.fetchAll();
  if (rates.length === 0) return;

  // Batch upsert. fx_rates is small (one row/business day/year), so a
  // single multi-row INSERT is simplest and fast enough.
  const values: string[] = [];
  const params: unknown[] = [];
  for (const r of rates) {
    params.push(r.date, r.audUsdRate, provider.sourceName);
    const i = params.length;
    values.push(`($${i - 2}, $${i - 1}, $${i})`);
  }
  await query(
    `INSERT INTO fx_rates (rate_date, aud_usd_rate, source)
     VALUES ${values.join(", ")}
     ON CONFLICT (rate_date) DO NOTHING`,
    params
  );
}

export type RateLookup = { rate: number; rateDate: string; source: string; requestedDate: string };

/**
 * Looks up the AUD/USD rate for a given date, falling back to the most
 * recent earlier date with a published rate (standard practice for
 * weekends/public holidays, when RBA doesn't publish a rate). Assumes
 * ensureFxRatesCached() has already been called for the relevant range.
 */
export async function getAudUsdRate(dateStr: string): Promise<RateLookup | null> {
  const row = await query<{ rate_date: string; aud_usd_rate: number; source: string }>(
    `SELECT rate_date, aud_usd_rate, source FROM fx_rates
     WHERE rate_date <= $1::date
     ORDER BY rate_date DESC LIMIT 1`,
    [dateStr]
  );
  if (row.length === 0) return null;
  return {
    rate: Number(row[0].aud_usd_rate),
    rateDate: String(row[0].rate_date).slice(0, 10),
    source: row[0].source,
    requestedDate: dateStr,
  };
}

/** AUD/USD convention: 1 AUD = audUsdRate USD, so AUD = USD / rate. */
export function usdToAud(usdAmount: number, audUsdRate: number): number {
  return usdAmount / audUsdRate;
}
