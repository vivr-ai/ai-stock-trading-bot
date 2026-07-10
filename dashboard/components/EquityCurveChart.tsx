"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { fmtMoney } from "@/lib/format";

export default function EquityCurveChart({
  data,
}: {
  data: { ts: string; value: number }[];
}) {
  const chartData = data.map((d) => ({
    ts: new Date(d.ts).getTime(),
    value: d.value,
  }));

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart data={chartData} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.35} />
            <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#232c3f" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="ts"
          type="number"
          domain={["dataMin", "dataMax"]}
          tickFormatter={(v) => new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          stroke="#8592a8"
          fontSize={11}
          tickLine={false}
        />
        <YAxis
          stroke="#8592a8"
          fontSize={11}
          tickLine={false}
          width={70}
          tickFormatter={(v) => fmtMoney(v)}
          domain={["auto", "auto"]}
        />
        <Tooltip
          contentStyle={{ background: "#171f30", border: "1px solid #232c3f", borderRadius: 8, fontSize: 12 }}
          labelFormatter={(v) => new Date(v).toLocaleString()}
          formatter={(v: number) => [fmtMoney(v), "Portfolio value"]}
        />
        <Area type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={2} fill="url(#equityFill)" />
      </AreaChart>
    </ResponsiveContainer>
  );
}
