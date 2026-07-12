"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { FileDown, FileArchive, Info } from "lucide-react";

type ListResponse = { availableFYs: string[] };

export default function AccountantExportPage() {
  const [fys, setFys] = useState<string[] | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/accountant-export");
        const json: ListResponse & { error?: string } = await res.json();
        if (!res.ok) throw new Error(json.error || "Request failed");
        setFys(json.availableFYs);
        setSelected(json.availableFYs[0] ?? "");
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function download() {
    if (!selected) return;
    setDownloading(true);
    setError(null);
    try {
      const res = await fetch(`/api/accountant-export?fy=${encodeURIComponent(selected)}`);
      if (!res.ok) {
        const json = await res.json().catch(() => ({}));
        throw new Error(json.error || "Export failed");
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `accountant-export-${selected}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Accountant Export</h1>
        <p className="text-sm text-muted">
          End-of-financial-year package for your accountant: realised trades converted to AUD at
          the RBA rate on each transaction date, an annual summary, open positions, and an audit
          trail of every FX rate used.
        </p>
      </div>

      {loading && <LoadingSkeleton rows={2} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && fys && fys.length === 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No closed trades yet - there&apos;s nothing to export until the bot has completed at
          least one trade.
        </div>
      )}

      {!loading && !error && fys && fys.length > 0 && (
        <div className="space-y-5">
          <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
            <label className="mb-2 block text-sm font-medium text-white">Financial year</label>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                className="rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-sm text-white outline-none focus:border-accent"
              >
                {fys.map((fy) => (
                  <option key={fy} value={fy}>
                    {fy} (1 Jul {fy.split("-")[0]} - 30 Jun {fy.split("-")[1]})
                  </option>
                ))}
              </select>
              <button
                onClick={download}
                disabled={downloading}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
              >
                <FileDown size={16} />
                {downloading ? "Generating..." : "Download ZIP package"}
              </button>
            </div>
          </div>

          <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
            <h2 className="mb-3 flex items-center gap-2 text-base font-semibold text-white">
              <FileArchive size={16} /> What&apos;s in the package
            </h2>
            <ul className="space-y-2 text-sm text-muted">
              <li><span className="text-white">trades_detailed.csv</span> — every realised trade, USD and AUD, with the FX rate used for each leg, days held, and a CGT 12-month-discount flag.</li>
              <li><span className="text-white">annual_summary.csv</span> — totals for the selected financial year.</li>
              <li><span className="text-white">open_positions_30_june.csv</span> — current open positions (a true 30 June snapshot only once that date has passed and been captured).</li>
              <li><span className="text-white">dividends.csv</span> — placeholder structure; populates automatically once the bot tracks real dividends.</li>
              <li><span className="text-white">fx_conversion_log.csv</span> — every RBA exchange rate actually used, for independent verification.</li>
              <li><span className="text-white">metadata.json</span> — generation time, bot version/commit, FX methodology, and a disclaimer.</li>
            </ul>
          </div>

          <div className="flex items-start gap-2.5 rounded-lg border border-accent/40 bg-accent/10 p-3 text-sm text-accent">
            <Info size={16} className="mt-0.5 shrink-0" />
            <span>
              Commission, withholding tax, and dividend figures are currently placeholders — this
              is a paper-trading account with no real fees or dividends yet. Verify all figures
              with a registered tax agent before lodging a return.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
