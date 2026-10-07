import { useQuery } from "@tanstack/react-query";
import { ComposedChart, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import { format } from "date-fns";
import { candlesQuery } from "@/lib/paperlab-api";

export function PriceChart({ program, symbol, tf, botKey }: { program: string; symbol: string; tf: string; botKey: string }) {
  const { data, isLoading } = useQuery(candlesQuery(program, symbol, tf));
  if (isLoading) return <p className="text-sm text-muted-foreground">Loading price chart…</p>;
  if (!data || data.candles.length === 0) return <p className="text-sm text-muted-foreground">No price data for {symbol}.</p>;
  const first = data.candles[0]!.ts;
  const last = data.candles[data.candles.length - 1]!.ts;
  const mine = data.markers.filter((m) => m.botKey === botKey && m.ts >= first && m.ts <= last);
  // Snap each marker onto the candle it falls in so price and markers share one data set.
  const rows: { ts: number; close: number; entry?: number; exit?: number }[] = data.candles.map((c) => ({ ts: c.ts, close: c.close }));
  for (const m of mine) {
    let i = rows.findIndex((r) => r.ts > m.ts) - 1;
    if (i < 0) i = rows.length - 1;
    if (m.kind === "entry") rows[i]!.entry = m.price;
    else rows[i]!.exit = m.price;
  }
  return (
    <>
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <XAxis dataKey="ts" type="number" domain={[first, last]} tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(t) => format(new Date(t), "MMM d HH:mm")} minTickGap={60} />
          <YAxis domain={["auto", "auto"]} tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} tickLine={false} axisLine={false} width={64} />
          <Tooltip
            contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--foreground)" }}
            labelFormatter={(t) => format(new Date(Number(t)), "MMM d, HH:mm")}
          />
          <Line dataKey="close" stroke="var(--chart-2)" dot={false} strokeWidth={1.5} isAnimationActive={false} name="Price" />
          <Scatter dataKey="entry" fill="var(--profit)" name="Entry" isAnimationActive={false} />
          <Scatter dataKey="exit" fill="var(--warn)" name="Exit" isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="mt-2 flex gap-4 text-xs text-muted-foreground">
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-profit" />Entry</span>
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-warn" />Exit</span>
        <span>{symbol} · {tf}</span>
      </div>
    </>
  );
}
