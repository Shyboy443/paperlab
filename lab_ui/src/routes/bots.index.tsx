import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useSuspenseQuery } from "@tanstack/react-query";
import { SourceBanner } from "@/components/lab/SourceBanner";
import { labQuery } from "@/lib/paperlab-api";
import { useBotName } from "@/lib/names";
import { Search } from "lucide-react";
import { AppShell } from "@/components/lab/AppShell";
import { Pct, Sparkline, StatusPill } from "@/components/lab/ui";
import type { Bot } from "@/lib/paperlab-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/bots/")({
  head: () => ({ meta: [{ title: "All bots — PaperLab" }, { name: "description", content: "Search, filter and sort every PaperLab trading bot." }] }),
  loader: ({ context }) => context.queryClient.ensureQueryData(labQuery()),
  errorComponent: ({ error }) => <AppShell><p role="alert" className="text-loss">{error instanceof Error ? error.message : "Something went wrong."}</p></AppShell>,
  notFoundComponent: () => <AppShell><p>Nothing here.</p></AppShell>,
  component: BotsPage,
});

type SortKey = "returnPct" | "maxDrawdown" | "winRate" | "sharpe" | "trades";
const sorts: { key: SortKey; label: string }[] = [
  { key: "returnPct", label: "Return" },
  { key: "sharpe", label: "Avg R" },
  { key: "winRate", label: "Win rate" },
  { key: "maxDrawdown", label: "Drawdown" },
  { key: "trades", label: "Trades" },
];

function BotsPage() {
  const { data } = useSuspenseQuery(labQuery());
  const { bots, programs } = data;
  const getProgram = (id: string) => programs.find((p) => p.id === id);
  const [q, setQ] = useState("");
  const [program, setProgram] = useState("all");
  const [onlyProfit, setOnlyProfit] = useState(false);
  const [sort, setSort] = useState<SortKey>("returnPct");
  const display = useBotName();

  const list = useMemo(() => {
    const s = q.toLowerCase();
    return bots
      .filter((b) => program === "all" || b.programId === program)
      .filter((b) => !onlyProfit || b.returnPct > 0)
      .filter((b) => !s || `${b.name} ${b.champion} ${b.key} ${b.symbol} ${b.persona}`.toLowerCase().includes(s))
      .sort((a: Bot, b: Bot) => (sort === "maxDrawdown" ? a[sort] - b[sort] : b[sort] - a[sort]));
  }, [bots, q, program, onlyProfit, sort]);

  const chip = (active: boolean) =>
    cn("rounded-full border px-3 py-1 text-sm transition-colors", active ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground");

  return (
    <AppShell>
      <h1 className="text-3xl font-semibold tracking-tight">Bots</h1>
      <p className="mt-1 mb-4 text-muted-foreground">Showing {list.length} of {bots.length}</p>
      <SourceBanner data={data} />

      <div className="panel mb-4 space-y-4 p-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-center">
          <label className="flex flex-1 items-center gap-2 rounded-lg border bg-surface px-3 py-2">
            <Search className="h-4 w-4 text-muted-foreground" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name, key, coin or style" className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground" />
          </label>
          <button onClick={() => setOnlyProfit((v) => !v)} className={chip(onlyProfit)}>Only in profit</button>
          <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className="rounded-lg border bg-surface px-3 py-2 text-sm">
            {sorts.map((s) => <option key={s.key} value={s.key}>Sort by {s.label}</option>)}
          </select>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => setProgram("all")} className={chip(program === "all")}>All programs</button>
          {programs.map((p) => (
            <button key={p.id} onClick={() => setProgram(p.id)} className={chip(program === p.id)}>{p.name.split(" ")[0]}</button>
          ))}
        </div>
      </div>

      <div className="panel overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="p-4">Bot</th>
              <th className="p-4">Program</th>
              <th className="p-4">Status</th>
              <th className="w-32 p-4">Trend</th>
              <th className="p-4 text-right">Return</th>
              <th className="p-4 text-right">Win rate</th>
              <th className="p-4 text-right">Max DD</th>
              <th className="p-4 text-right">Avg R</th>
            </tr>
          </thead>
          <tbody>
            {list.map((b) => (
              <tr key={b.id} className="border-b last:border-0 hover:bg-muted/50">
                <td className="p-4">
                  <Link to="/bots/$botId" params={{ botId: b.id }} className="font-medium hover:text-primary">{display(b)}</Link>
                  <div className="text-xs text-muted-foreground">{b.key} · {b.symbol}</div>
                </td>
                <td className="p-4 text-muted-foreground">{getProgram(b.programId)?.name}</td>
                <td className="p-4"><StatusPill status={b.status} /></td>
                <td className="p-4"><Sparkline data={b.equity} /></td>
                <td className="p-4 text-right"><Pct value={b.returnPct} /></td>
                <td className="num p-4 text-right">{b.winRate}%</td>
                <td className="num p-4 text-right text-loss">-{b.maxDrawdown}%</td>
                <td className="num p-4 text-right">{b.sharpe}</td>
              </tr>
            ))}
            {list.length === 0 && (
              <tr><td colSpan={8} className="p-10 text-center text-muted-foreground">No bots match these filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </AppShell>
  );
}
