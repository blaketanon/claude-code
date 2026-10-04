import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export function EquityChart({ points, height = 260 }: { points: { ts: string; equity: number }[]; height?: number }) {
  if (!points?.length) return <div className="muted" style={{ height, display: "grid", placeItems: "center" }}>No equity history yet. Start the engine or run a paper step.</div>;
  const data = points.map((p) => ({ t: new Date(p.ts).getTime(), equity: p.equity }));
  const min = Math.min(...data.map((d) => d.equity)), max = Math.max(...data.map((d) => d.equity));
  const pad = (max - min) * 0.1 || 1;
  const digits = max - min < 50 ? 2 : 0;
  const fmtTick = (v: number) => Number(v).toLocaleString(undefined, { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits });
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 12, left: 4, bottom: 0 }}>
        <defs>
          <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--series-1)" stopOpacity={0.25} />
            <stop offset="100%" stopColor="var(--series-1)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(t) => new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" })} stroke="var(--text-muted)" tick={{ fill: "var(--text-secondary)", fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={40} />
        <YAxis domain={[min - pad, max + pad]} tickFormatter={fmtTick} stroke="var(--text-muted)" tick={{ fill: "var(--text-secondary)", fontSize: 11 }} tickLine={false} axisLine={false} width={86} />
        <Tooltip contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text-primary)" }} labelFormatter={(t) => new Date(Number(t)).toLocaleString()} formatter={(v: any) => [Number(v).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }), "Equity"]} cursor={{ stroke: "var(--text-muted)", strokeWidth: 1 }} />
        <Area type="monotone" dataKey="equity" stroke="var(--series-1)" strokeWidth={2} fill="url(#eqFill)" dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-1)" }} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
