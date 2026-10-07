import { createFileRoute, Link, notFound } from "@tanstack/react-router";
import { useSuspenseQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { format } from "date-fns";
import { AppShell } from "@/components/lab/AppShell";
import { EquityChart, Pct, Stat, StatusPill } from "@/components/lab/ui";
import { labQuery, tradesQuery } from "@/lib/paperlab-api";
import { useBotName, useOtherName } from "@/lib/names";
import { cn } from "@/lib/utils";
import { PriceChart } from "@/components/lab/PriceChart";

export const Route = createFileRoute("/bots/$botId")({
  loader: async ({ context, params }) => {
    const lab = await context.queryClient.ensureQueryData(labQuery());
    const bot = lab.bots.find((b) => b.id === params.botId);
    if (!bot) throw notFound();
    await context.queryClient.ensureQueryData(tradesQuery(bot.id));
    return { name: bot.name, symbol: bot.symbol };
  },
  head: ({ loaderData }) => ({
    meta: [{ title: loaderData ? `${loaderData.name} — PaperLab bot` : "Bot not found — PaperLab" }],
  }),
  errorComponent: ({ error }) => <AppShell><p role="alert" className="text-loss">{error instanceof Error ? error.message : "Something went wrong."}</p></AppShell>,
  notFoundComponent: BotNotFound,
  component: BotDetail,
});

function BotNotFound() {
  return (
    <AppShell>
      <p className="text-muted-foreground">This bot doesn't exist.</p>
      <Link to="/bots" className="text-primary">Back to bots</Link>
    </AppShell>
  );
}

function BotDetail() {
  const { botId } = Route.useParams();
  const { data: lab } = useSuspenseQuery(labQuery());
  const bot = lab.bots.find((b) => b.id === botId);
  if (!bot) return <BotNotFound />;
  return <BotView botId={botId} />;
}

function BotView({ botId }: { botId: string }) {
  const { data: lab } = useSuspenseQuery(labQuery());
  const display = useBotName();
  const other = useOtherName();
  const bot = lab.bots.find((b) => b.id === botId)!;
  const program = lab.programs.find((p) => p.id === bot.programId);
  const aka = other(bot);
  const costEats = bot.grossPct > 0 && bot.returnPct < 0;

  return (
    <AppShell>
      <Link to="/bots" className="mb-6 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> All bots
      </Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-semibold tracking-tight">{display(bot)}</h1>
            <StatusPill status={bot.status} />
          </div>
          <p className="mt-1 text-muted-foreground"><span className="capitalize">{bot.persona}</span> · {program?.name} · {bot.symbol} · {bot.variant} variant</p>
          <p className="num mt-1 text-xs text-muted-foreground">{bot.key}{aka ? ` · also shown as ${aka}` : ""}{bot.stage ? ` · ${bot.stage.toLowerCase()}` : ""}</p>
        </div>
        <div className="text-right">
          <div className="text-xs uppercase tracking-wider text-muted-foreground">Net return</div>
          <Pct value={bot.returnPct} className="text-3xl font-semibold" />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="Before costs"><Pct value={bot.grossPct} /></Stat>
        <Stat label="Costs paid"><span className="num text-warn">-{bot.feesPct}%</span></Stat>
        <Stat label="Win rate"><span className="num">{bot.winRate}%</span></Stat>
        <Stat label="Max drawdown"><span className="num text-loss">-{bot.maxDrawdown}%</span></Stat>
        <Stat label="Trades" hint={`Avg R ${bot.sharpe}`}><span className="num">{bot.trades}</span></Stat>
      </div>

      {costEats && (
        <div className="mt-4 rounded-xl border border-warn/30 bg-warn/10 p-4 text-sm text-warn">
          This bot is profitable before costs, but fees and spread turn it into a loss. Fewer, larger trades or maker entries may help.
        </div>
      )}

      <section className="panel mt-6 p-5">
        <h2 className="mb-4 font-medium">Equity curve</h2>
        <EquityChart data={bot.equity} height={300} />
      </section>

      {/^[A-Z0-9]{2,20}$/.test(bot.symbol) && bot.timeframe && (
        <section className="panel mt-6 p-5">
          <h2 className="mb-4 font-medium">Price and this bot's trades {bot.openPositions ? <span className="ml-2 text-xs text-profit">{bot.openPositions} open position{bot.openPositions > 1 ? "s" : ""}</span> : null}</h2>
          <PriceChart program={bot.programId} symbol={bot.symbol} tf={bot.timeframe} botKey={bot.key} />
        </section>
      )}


      <section className="panel mt-6 overflow-x-auto">
        <h2 className="p-5 pb-2 font-medium">Recent trades</h2>
        <LiveTrades botId={botId} />
      </section>
    </AppShell>
  );
}

function LiveTrades({ botId }: { botId: string }) {
  const { data } = useSuspenseQuery(tradesQuery(botId));
  if (!data) return <p className="p-5 text-sm text-muted-foreground">Couldn't load trades from PaperLab right now.</p>;
  return <TradesTable trades={data} />;
}

function TradesTable({ trades }: { trades: { id: string; time: string; symbol: string; side: string; kind: string; price: number; fee: number; pnl: number; pnlUnit: "pct" | "usd" }[] }) {
  if (trades.length === 0) return <p className="p-5 text-sm text-muted-foreground">No trades yet.</p>;
  return (
    <table className="w-full text-sm">
      <thead className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
        <tr>
          <th className="p-4">Time (UTC)</th>
          <th className="p-4">Symbol</th>
          <th className="p-4">Side</th>
          <th className="p-4">Type</th>
          <th className="p-4 text-right">Price</th>
          <th className="p-4 text-right">Fee</th>
          <th className="p-4 text-right">Result</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t) => (
          <tr key={t.id} className="border-b last:border-0">
            <td className="num p-4 text-muted-foreground">{format(new Date(t.time), "MMM d, HH:mm")}</td>
            <td className="p-4">{t.symbol}</td>
            <td className="p-4"><span className={t.side === "long" ? "text-profit" : "text-loss"}>{t.side}</span></td>
            <td className="p-4 text-muted-foreground">{t.kind}</td>
            <td className="num p-4 text-right">{t.price}</td>
            <td className="num p-4 text-right text-muted-foreground">{t.fee}{t.pnlUnit === "pct" ? "%" : ""}</td>
            <td className="p-4 text-right">
              {t.pnlUnit === "pct" ? (
                <Pct value={t.pnl} />
              ) : (
                <span className={cn("num", t.pnl >= 0 ? "text-profit" : "text-loss")}>
                  {t.pnl > 0 ? "+" : ""}{t.pnl.toFixed(2)} USDT
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
