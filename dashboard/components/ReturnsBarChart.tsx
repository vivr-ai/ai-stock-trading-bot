"use client";

import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts";

export default function ReturnsBarChart({
  data,
}: {
  data: { label: string; pct: number }[];
}) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#232c3f" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" stroke="#8592a8" fontSize={11} tickLine={false} />
        <YAxis
          stroke="#8592a8"
          fontSize={11}
          tickLine={false}
          width={50}
          tickFormatter={(v) => `${v}%`}
        />
        <Tooltip
          contentStyle={{ background: "#171f30", border: "1px solid #232c3f", borderRadius: 8, fontSize: 12 }}
          formatter={(v: number) => [`${v >= 0 ? "+" : ""}${v.toFixed(2)}%`, "Return"]}
        />
        <Bar dataKey="pct" radius={[3, 3, 0, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.pct >= 0 ? "#22c55e" : "#ef4444"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
