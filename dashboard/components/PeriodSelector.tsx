"use client";

import { RANGE_OPTIONS, type RangeKey } from "@/lib/periodRanges";

/** Yahoo Finance-style segmented range picker (1W/1M/3M/6M/YTD/1Y/FY/All). */
export default function PeriodSelector({
  value,
  onChange,
}: {
  value: RangeKey;
  onChange: (key: RangeKey) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-bg-border bg-bg-panel2 p-0.5">
      {RANGE_OPTIONS.map((opt) => {
        const active = opt.key === value;
        return (
          <button
            key={opt.key}
            type="button"
            onClick={() => onChange(opt.key)}
            aria-pressed={active}
            className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
              active
                ? "bg-accent text-white"
                : "text-muted hover:text-white"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
