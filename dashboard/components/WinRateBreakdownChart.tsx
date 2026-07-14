"use client";

import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts";

type Bucket = {
  key: string;
  trades: number;
  winRatePct: number | null;
  avgPnl: number | null;
  totalPnl: number;
  sufficientSample: boolean;
};

export default function WinRateBreakdownChart({ data }: { data: Bucket[] }) {
  const chartData = data
    .filter((d) => d.winRatePct != null)
    .map((d) => ({ ...d, winRatePct: d.winRatePct as number }));

  if (chartData.length === 0) {
    return <div className="py-10 text-center text-sm text-muted">Not enough history yet.</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={chartData} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#232c3f" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="key" stroke="#8592a8" fontSize={11} tickLine={false} />
        <YAxis
          stroke="#8592a8"
          fontSize={11}
          tickLine={false}
          width={50}
          tickFormatter={(v) => `${v}%`}
          domain={[0, 100]}
        />
        <Tooltip
          contentStyle={{ background: "#171f30", border: "1px solid #232c3f", borderRadius: 8, fontSize: 12 }}
          formatter={(v: number, _name, item) => [
            `${v.toFixed(1)}% (${item.payload.trades} trades${
              item.payload.sufficientSample ? "" : " - low sample"
            })`,
            "Win rate",
          ]}
        />
        <Bar dataKey="winRatePct" radius={[3, 3, 0, 0]}>
          {chartData.map((d, i) => (
            <Cell key={i} fill={d.sufficientSample ? "#3b82f6" : "#8592a8"} fillOpacity={d.sufficientSample ? 1 : 0.5} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
