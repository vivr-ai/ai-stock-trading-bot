"use client";

import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

type HistoryPoint = { computed_at: string; overall_score: number | null; confidence_level: string };

export default function HealthTrendChart({ data }: { data: HistoryPoint[] }) {
  const chartData = data
    .filter((d) => d.overall_score != null)
    .map((d) => ({
      date: new Date(d.computed_at).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
      score: Number(d.overall_score),
      confidence: d.confidence_level,
    }));

  if (chartData.length < 2) {
    return (
      <div className="py-10 text-center text-sm text-muted">
        Not enough history yet to chart a trend - check back after a few more computations.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={chartData} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#232c3f" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="date" stroke="#8592a8" fontSize={11} tickLine={false} />
        <YAxis stroke="#8592a8" fontSize={11} tickLine={false} width={36} domain={[0, 100]} />
        <Tooltip
          contentStyle={{ background: "#171f30", border: "1px solid #232c3f", borderRadius: 8, fontSize: 12 }}
          formatter={(v: number, _name, item) => [`${v.toFixed(0)} (${item.payload.confidence} confidence)`, "Health score"]}
        />
        <Line type="monotone" dataKey="score" stroke="#3b82f6" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
