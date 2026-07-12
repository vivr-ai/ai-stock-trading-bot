import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import JSZip from "jszip";
import { authOptions } from "@/lib/auth";
import { query, queryOne } from "@/lib/db";
import { ensureFxRatesCached, getAudUsdRate, usdToAud, type RateLookup } from "@/lib/fx";
import { fyDateRange, isFyComplete, listAvailableFYs, parseFyLabel } from "@/lib/financialYear";

export const dynamic = "force-dynamic";

type ClosedTrade = {
  symbol: string;
  qty: number;
  entry_price: number;
  exit_price: number;
  pnl: number;
  pnl_pct: number;
  exit_reason: string | null;
  entry_time: string | null;
  ts: string; // exit time
  buy_reason: string | null;
};

type OpenPosition = {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  current_price: number | null;
  market_value: number | null;
  unrealized_pl: number | null;
  entry_time: string | null;
  entry_reason: string | null;
};

function csvEscape(value: unknown): string {
  if (value === null || value === undefined) return "";
  const s = String(value);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function toCsv(headers: string[], rows: (string | number | null)[][]): string {
  const lines = [headers.map(csvEscape).join(",")];
  for (const row of rows) lines.push(row.map(csvEscape).join(","));
  return lines.join("\r\n");
}

function dateOnly(ts: string | Date): string {
  const s = ts instanceof Date ? ts.toISOString() : ts;
  return s.slice(0, 10);
}

export async function GET(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(req.url);
  const fyParam = searchParams.get("fy");

  try {
    // Mode 1: no `fy` given - just list what financial years have data,
    // so the dashboard page can populate its picker.
    if (!fyParam) {
      const earliest = await queryOne<{ ts: string }>(
        "SELECT ts FROM closed_trades ORDER BY ts ASC LIMIT 1"
      );
      return NextResponse.json({
        availableFYs: listAvailableFYs(earliest?.ts ?? null),
      });
    }

    const startYear = parseFyLabel(fyParam);
    if (startYear === null) {
      return NextResponse.json(
        { error: "Invalid fy parameter - expected format YYYY-YYYY, e.g. 2025-2026" },
        { status: 400 }
      );
    }
    const { start, end } = fyDateRange(startYear);
    const complete = isFyComplete(startYear);

    // Make sure we have FX coverage for the whole FY before doing any lookups.
    await ensureFxRatesCached(start, end);

    const closedTrades = await query<ClosedTrade>(
      `SELECT symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
              entry_time, ts, buy_reason
       FROM closed_trades
       WHERE ts >= $1::date AND ts <= ($2::date + interval '1 day')
       ORDER BY ts ASC`,
      [start, end]
    );

    const fxLookupsUsed = new Map<string, RateLookup>();
    async function rateFor(dateIso: string): Promise<RateLookup> {
      const cached = fxLookupsUsed.get(dateIso);
      if (cached) return cached;
      const looked = await getAudUsdRate(dateIso);
      const result: RateLookup = looked ?? {
        rate: NaN,
        rateDate: dateIso,
        source: "unavailable",
        requestedDate: dateIso,
      };
      fxLookupsUsed.set(dateIso, result);
      return result;
    }

    // ---- trades_detailed.csv --------------------------------------------
    const tradeRows: (string | number | null)[][] = [];
    let totalProceedsAud = 0;
    let totalCostBaseAud = 0;
    let totalGainAud = 0;
    let wins = 0;

    for (const t of closedTrades) {
      const exitDate = dateOnly(t.ts);
      const entryDate = t.entry_time ? dateOnly(t.entry_time) : exitDate;
      const exitRate = await rateFor(exitDate);
      const entryRate = await rateFor(entryDate);

      const qty = Number(t.qty);
      const proceedsUsd = Number(t.exit_price) * qty;
      const costBaseUsd = Number(t.entry_price) * qty;
      const proceedsAud = Number.isFinite(exitRate.rate) ? usdToAud(proceedsUsd, exitRate.rate) : null;
      const costBaseAud = Number.isFinite(entryRate.rate) ? usdToAud(costBaseUsd, entryRate.rate) : null;
      const gainAud = proceedsAud != null && costBaseAud != null ? proceedsAud - costBaseAud : null;
      const daysHeld =
        t.entry_time != null
          ? Math.round((new Date(t.ts).getTime() - new Date(t.entry_time).getTime()) / 86_400_000)
          : null;

      if (gainAud != null) {
        totalProceedsAud += proceedsAud ?? 0;
        totalCostBaseAud += costBaseAud ?? 0;
        totalGainAud += gainAud;
        if (gainAud > 0) wins += 1;
      }

      tradeRows.push([
        t.symbol,
        qty,
        entryDate,
        Number(t.entry_price).toFixed(4),
        entryRate.rate.toFixed(4),
        costBaseUsd.toFixed(2),
        costBaseAud != null ? costBaseAud.toFixed(2) : "",
        exitDate,
        Number(t.exit_price).toFixed(4),
        exitRate.rate.toFixed(4),
        proceedsUsd.toFixed(2),
        proceedsAud != null ? proceedsAud.toFixed(2) : "",
        gainAud != null ? gainAud.toFixed(2) : "",
        Number(t.pnl).toFixed(2), // USD gain, straight from the bot
        daysHeld,
        daysHeld != null && daysHeld > 365 ? "yes" : "no",
        t.exit_reason ?? "",
        t.buy_reason ?? "",
        "", // commission_usd - placeholder, see NOTES
        "", // commission_aud - placeholder
        "", // withholding_tax_usd - placeholder
      ]);
    }

    const tradesCsv = toCsv(
      [
        "symbol", "qty",
        "entry_date", "entry_price_usd", "entry_fx_rate_aud_usd", "cost_base_usd", "cost_base_aud",
        "exit_date", "exit_price_usd", "exit_fx_rate_aud_usd", "proceeds_usd", "proceeds_aud",
        "capital_gain_aud", "capital_gain_usd",
        "days_held", "cgt_discount_eligible_over_12mo",
        "exit_reason", "entry_reason",
        "commission_usd_placeholder", "commission_aud_placeholder", "withholding_tax_usd_placeholder",
      ],
      tradeRows
    );

    // ---- annual_summary.csv ----------------------------------------------
    const totalTrades = closedTrades.length;
    const winRatePct = totalTrades > 0 ? (wins / totalTrades) * 100 : null;
    const summaryCsv = toCsv(
      [
        "financial_year", "total_closed_trades", "total_proceeds_aud", "total_cost_base_aud",
        "total_capital_gain_aud", "win_count", "win_rate_pct", "period_status",
      ],
      [[
        fyParam, totalTrades, totalProceedsAud.toFixed(2), totalCostBaseAud.toFixed(2),
        totalGainAud.toFixed(2), wins, winRatePct != null ? winRatePct.toFixed(1) : "",
        complete ? "complete" : "in progress (up to export date)",
      ]]
    );

    // ---- open_positions_30_june.csv --------------------------------------
    let positionsCsv: string;
    if (!complete) {
      const positions = await query<OpenPosition>(
        "SELECT symbol, qty, avg_entry_price, current_price, market_value, unrealized_pl, entry_time, entry_reason FROM open_positions ORDER BY symbol"
      );
      const rows: (string | number | null)[][] = [];
      for (const p of positions) {
        const entryDate = p.entry_time ? dateOnly(p.entry_time) : "";
        const entryRate = entryDate ? await rateFor(entryDate) : null;
        const costBaseUsd = Number(p.avg_entry_price) * Number(p.qty);
        const costBaseAud = entryRate && Number.isFinite(entryRate.rate) ? usdToAud(costBaseUsd, entryRate.rate) : null;
        rows.push([
          p.symbol, p.qty, Number(p.avg_entry_price).toFixed(4), entryDate,
          costBaseUsd.toFixed(2), costBaseAud != null ? costBaseAud.toFixed(2) : "",
          p.current_price != null ? Number(p.current_price).toFixed(4) : "",
          p.market_value != null ? Number(p.market_value).toFixed(2) : "",
          p.unrealized_pl != null ? Number(p.unrealized_pl).toFixed(2) : "",
        ]);
      }
      positionsCsv =
        `NOTE: This financial year has not yet ended - these are the positions currently open as of export generation, not a true 30 June snapshot.\r\n` +
        toCsv(
          ["symbol", "qty", "avg_entry_price_usd", "entry_date", "cost_base_usd", "cost_base_aud",
           "current_price_usd", "market_value_usd", "unrealized_pl_usd"],
          rows
        );
    } else {
      positionsCsv =
        "NOTE: Historical end-of-financial-year position snapshots were not retained for this period " +
        "(this feature only began capturing point-in-time snapshots from when it was introduced). " +
        "This can be added going forward via a scheduled 30 June snapshot job.\r\n" +
        toCsv(["symbol", "qty", "avg_entry_price_usd", "entry_date", "cost_base_usd", "cost_base_aud",
               "current_price_usd", "market_value_usd", "unrealized_pl_usd"], []);
    }

    // ---- dividends.csv (placeholder - see NOTES) --------------------------
    const dividendsCsv =
      "NOTE: Dividend data is not yet tracked by the bot. This file is a placeholder with the " +
      "intended structure, ready to populate automatically once dividend recording is added " +
      "(e.g. from Alpaca's account activities API).\r\n" +
      toCsv(
        ["symbol", "pay_date", "amount_usd", "fx_rate_aud_usd", "amount_aud", "withholding_tax_usd_placeholder", "franking_credits_placeholder"],
        []
      );

    // ---- fx_conversion_log.csv --------------------------------------------
    const fxRows = Array.from(fxLookupsUsed.values())
      .sort((a, b) => a.requestedDate.localeCompare(b.requestedDate))
      .map((r) => [
        r.requestedDate,
        Number.isFinite(r.rate) ? r.rate.toFixed(4) : "UNAVAILABLE",
        r.rateDate !== r.requestedDate ? r.rateDate : "",
        r.source,
      ]);
    const fxCsv = toCsv(
      ["transaction_date", "aud_usd_rate", "rate_actually_from_date_if_different", "source"],
      fxRows
    );

    // ---- metadata.json ------------------------------------------------------
    const lastDeploy = await queryOne<{ ts: string; metadata: { commit_short?: string } | null }>(
      "SELECT ts, metadata FROM notifications WHERE type = 'deployment_completed' ORDER BY ts DESC LIMIT 1"
    );
    const metadata = {
      generated_at: new Date().toISOString(),
      financial_year: fyParam,
      period_status: complete ? "complete" : "in progress",
      bot_commit: lastDeploy?.metadata?.commit_short ?? null,
      bot_last_deployed_at: lastDeploy?.ts ?? null,
      fx_rate_source: "Reserve Bank of Australia, F11.1 daily series (Series ID FXRUSD)",
      fx_conversion_method:
        "Each trade leg (entry and exit) converted independently using the AUD/USD rate on its own " +
        "transaction date, then subtracted - not a single blended rate applied to the net USD profit.",
      disclaimer:
        "Generated automatically for record-keeping convenience. Verify figures, especially FX rates " +
        "and CGT discount eligibility, with a registered tax agent before lodging a return. Commission, " +
        "dividend, and withholding tax columns are placeholders pending live trading and are not yet " +
        "populated with real data.",
    };

    const zip = new JSZip();
    zip.file("trades_detailed.csv", tradesCsv);
    zip.file("annual_summary.csv", summaryCsv);
    zip.file("open_positions_30_june.csv", positionsCsv);
    zip.file("dividends.csv", dividendsCsv);
    zip.file("fx_conversion_log.csv", fxCsv);
    zip.file("metadata.json", JSON.stringify(metadata, null, 2));

    const buffer = await zip.generateAsync({ type: "nodebuffer" });
    return new NextResponse(buffer, {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": `attachment; filename="accountant-export-${fyParam}.zip"`,
      },
    });
  } catch (err) {
    console.error("GET /api/accountant-export failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
