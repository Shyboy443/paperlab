import { createFileRoute, Link } from "@tanstack/react-router";
import { useSuspenseQuery } from "@tanstack/react-query";
import { ArrowUpRight, Rocket } from "lucide-react";
import { formatDistanceToNowStrict } from "date-fns";
import { AppShell } from "@/components/lab/AppShell";
import { SourceBanner } from "@/components/lab/SourceBanner";
import { EquityChart, Pct, Sparkline, Stat } from "@/components/lab/ui";
import { labEquity } from "@/lib/paperlab-data";
import { labQuery, rosterQuery, type RosterBot } from "@/lib/paperlab-api";
import { useBotName } from "@/lib/names";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({ meta: [{ title: "PaperLab — Lab overview" }, { name: "description", content: "Read-only live view of every forward-tested PaperLab trading bot." }] }),
  loader: ({ context }) =>
    Promise.all([context.queryClient.ensureQueryData(labQuery()), context.queryClient.ensureQueryData(rosterQuery())]),
  errorComponent: ({ error }) => <AppShell><p role="alert" className="text-loss">{error instanceof Error ? error.message : "Something went wrong."}</p></AppShell>,
  notFoundComponent: () => <AppShell><p>Nothing here.</p></AppShell>,
  component: Overview,
});

function Overview() {
  const { data: lab } = useSuspenseQuery(labQuery());
  const { data: roster } = useSuspenseQuery(rosterQuery());
  const display = useBotName();
  const nameOf = (b: RosterBot) => {
    const bot = lab.bots.find((x) => x.id === b.id);
    return bot ? display(bot) : b.name;
  };
  const exists = (id: string) => lab.bots.some((x) => x.id === id);
  const curve = labEquity(lab.bots);
  const labReturn = curve[curve.length - 1]!.value - 100;
  const k = roster.kpis;

  return (
    <AppShell>
      <div className="mb-4">
        <h1 className="text-3xl font-semibold tracking-tight">Lab overview</h1>
        <p className="mt-1 text-muted-foreground">{k.botsTotal || lab.bots.length} bots forward-testing on live prices with simulated fills.</p>
      </div>
      <SourceBanner data={lab} />
      {!roster.ok && <div className="mb-4 rounded-xl bg-loss/10 p-3 text-sm text-loss">Couldn't load the home feed: {roster.error}</div>}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Net, all bots" hint="USDT after every cost"><span className={cn("num", k.allNet >= 0 ? "text-profit" : "text-loss")}>{k.allNet >= 0 ? "+" : ""}{k.allNet.toFixed(2)}</span></Stat>
        <Stat label="Closed trades" hint="All programs"><span className="num">{k.allTrades}</span></Stat>
        <Stat label="Bots in profit" hint={`of ${k.botsTotal}`}><span className="num text-profit">{k.inProfit}</span></Stat>
        <Stat label="Open positions" hint="Right now"><span className="num">{k.positionsOpen}</span></Stat>
      </div>

      <section className="mt-6">
        <div className="mb-1 flex items-end justify-between">
          <h2 className="text-lg font-medium">On stage</h2>
          <Link to="/bots" className="flex items-center gap-1 text-sm text-primary hover:underline">All bots <ArrowUpRight className="h-4 w-4" /></Link>
        </div>
        {roster.rule && <p className="mb-4 max-w-3xl text-xs text-muted-foreground">{roster.rule}</p>}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {roster.onStage.map((b) => <RosterCard key={b.id} b={b} name={nameOf(b)} linkable={exists(b.id)} />)}
        </div>
      </section>

      {roster.ready.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-1 flex items-center gap-2 text-lg font-medium"><Rocket className="h-4 w-4 text-primary" /> Ready to go live</h2>
          <p className="mb-4 text-xs text-muted-foreground">V6 bots that passed the two-year backtest. Going live is done by the operator in the <a href="/" className="text-primary hover:underline">operator console</a>.</p>
          <div className="panel overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
                <tr><th className="p-4">Bot</th><th className="p-4">Market</th><th className="p-4 text-right">Live return</th><th className="p-4 text-right">Backtest return</th><th className="p-4 text-right">Backtest PF</th><th className="p-4 text-right">Backtest max DD</th><th className="p-4 text-right">Backtest trades</th></tr>
              </thead>
              <tbody>
                {roster.ready.map((b) => (
                  <tr key={b.id} className="border-b last:border-0">
                    <td className="p-4">
                      {exists(b.id) ? <Link to="/bots/$botId" params={{ botId: b.id }} className="font-medium hover:text-primary">{nameOf(b)}</Link> : <span className="font-medium">{nameOf(b)}</span>}
                      <div className="text-xs text-muted-foreground">{b.persona}</div>
                    </td>
                    <td className="p-4 text-muted-foreground">{b.where}</td>
                    <td className="p-4 text-right"><Pct value={b.returnPct} /></td>
                    <td className="p-4 text-right">{b.backtest ? <Pct value={b.backtest.returnPct} /> : "—"}</td>
                    <td className="num p-4 text-right">{b.backtest?.profitFactor.toFixed(2) ?? "—"}</td>
                    <td className="num p-4 text-right text-loss">{b.backtest ? `-${b.backtest.maxDrawdown}%` : "—"}</td>
                    <td className="num p-4 text-right">{b.backtest?.trades ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-3">
        <section className="panel p-5 lg:col-span-2">
          <h2 className="font-medium">Lab equity (average of all bots)</h2>
          <p className="mb-4 text-sm text-muted-foreground">Since the forward test started · 100 = starting balance · <Pct value={labReturn} /></p>
          <EquityChart data={curve} />
        </section>
        <section className="panel p-5">
          <h2 className="mb-4 font-medium">Recent trade ideas</h2>
          {roster.ideas.length === 0 ? <p className="text-sm text-muted-foreground">No ideas yet.</p> : (
            <ul className="space-y-3">
              {roster.ideas.map((i, n) => (
                <li key={`${n}-${i.program}-${i.ts}-${i.symbol}-${i.side}`} className="rounded-lg border bg-surface p-3 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{i.symbol} <span className={i.side === "long" ? "text-profit" : "text-loss"}>{i.side}</span></span>
                    <span className="text-xs text-muted-foreground">{i.program.toUpperCase()} · {formatDistanceToNowStrict(new Date(i.ts))} ago</span>
                  </div>
                  <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                    {i.bots.map((b, j) => (
                      <div key={`${j}-${b.name}`}>
                        <span className={b.verdict === "TAKE" ? "text-profit" : "text-warn"}>{b.verdict}</span> · {b.reason}
                        {b.net != null && <span className={cn("num ml-1", b.net >= 0 ? "text-profit" : "text-loss")}>{b.net >= 0 ? "+" : ""}{b.net.toFixed(3)} USDT</span>}
                      </div>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </AppShell>
  );
}

function RosterCard({ b, name, linkable }: { b: RosterBot; name: string; linkable: boolean }) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium group-hover:text-primary">{name}</div>
          <div className="text-xs text-muted-foreground">{b.persona} · {b.programLabel} · {b.where}</div>
        </div>
        <Pct value={b.returnPct} className="font-semibold" />
      </div>
      <div className="my-3"><Sparkline data={b.equityCurve} /></div>
      <div className="grid grid-cols-4 gap-2 text-xs">
        <div><div className="text-muted-foreground">Net</div><span className={cn("num", b.net >= 0 ? "text-profit" : "text-loss")}>{b.net.toFixed(2)}</span></div>
        <div><div className="text-muted-foreground">Trades</div><span className="num">{b.trades}</span></div>
        <div><div className="text-muted-foreground">Win</div><span className="num">{b.winRate}%</span></div>
        <div><div className="text-muted-foreground">PF</div><span className="num">{b.profitFactor >= 999 ? "∞" : b.profitFactor}</span></div>
      </div>
      {b.positions.length > 0 && (
        <div className="mt-3 border-t pt-2 text-xs">
          {b.positions.map((p, i) => (
            <div key={i} className="flex justify-between">
              <span>Open {p.side} {p.symbol}</span>
              <span className={cn("num", p.upnl >= 0 ? "text-profit" : "text-loss")}>{p.upnl >= 0 ? "+" : ""}{p.upnl.toFixed(3)}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
  return linkable ? (
    <Link to="/bots/$botId" params={{ botId: b.id }} className="panel group block p-5 transition-colors hover:border-primary/40">{body}</Link>
  ) : (
    <div className="panel p-5">{body}</div>
  );
}
