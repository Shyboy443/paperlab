import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { cn } from "@/lib/utils";
import type { BotStatus } from "@/lib/paperlab-data";

export function Pct({ value, className }: { value: number; className?: string }) {
  return (
    <span className={cn("num", value >= 0 ? "text-profit" : "text-loss", className)}>
      {value > 0 ? "+" : ""}
      {value.toFixed(2)}%
    </span>
  );
}

export function Stat({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="panel p-5">
      <div className="text-xs uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="num mt-2 text-2xl font-semibold">{children}</div>
      {hint && <div className="mt-1 text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function StatusPill({ status }: { status: BotStatus }) {
  const styles = {
    running: "bg-profit/10 text-profit",
    paused: "bg-warn/10 text-warn",
    eliminated: "bg-loss/10 text-loss",
    frozen: "bg-muted text-muted-foreground",
  }[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium capitalize", styles)}>
      {status === "running" && <span className="live-dot !h-1.5 !w-1.5" />}
      {status}
    </span>
  );
}

export function EquityChart({ data, height = 260 }: { data: { day: number; value: number }[]; height?: number }) {
  const up = data[data.length - 1]!.value >= data[0]!.value;
  const color = up ? "var(--profit)" : "var(--loss)";
  const id = `g-${up ? "up" : "dn"}-${height}`;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="day" tickLine={false} axisLine={false} tick={false} height={8} />
        <YAxis tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} tickLine={false} axisLine={false} domain={["auto", "auto"]} width={48} />
        <Tooltip
          contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--foreground)" }}
          labelFormatter={() => ""}
          formatter={(v: number) => [v.toFixed(2), "Equity"]}
        />
        <Area type={data.length < 12 ? "linear" : "monotone"} dataKey="value" stroke={color} strokeWidth={2} fill={`url(#${id})`} animationDuration={900} animationEasing="ease-out" />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function Sparkline({ data }: { data: { day: number; value: number }[] }) {
  const up = data[data.length - 1]!.value >= data[0]!.value;
  return (
    <ResponsiveContainer width="100%" height={36}>
      <AreaChart data={data}>
        <YAxis hide domain={["dataMin", "dataMax"]} />
        <Area type="monotone" dataKey="value" stroke={up ? "var(--profit)" : "var(--loss)"} strokeWidth={1.5} fill="transparent" isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
