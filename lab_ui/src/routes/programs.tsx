import { createFileRoute, Link } from "@tanstack/react-router";
import { AppShell } from "@/components/lab/AppShell";
import { Pct } from "@/components/lab/ui";
import { useSuspenseQuery } from "@tanstack/react-query";
import { SourceBanner } from "@/components/lab/SourceBanner";
import { labQuery } from "@/lib/paperlab-api";
import { useBotName } from "@/lib/names";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/programs")({
  head: () => ({ meta: [{ title: "Research programs — PaperLab" }, { name: "description", content: "Every PaperLab research program and its verdict." }] }),
  loader: ({ context }) => context.queryClient.ensureQueryData(labQuery()),
  errorComponent: ({ error }) => <AppShell><p role="alert" className="text-loss">{error instanceof Error ? error.message : "Something went wrong."}</p></AppShell>,
  notFoundComponent: () => <AppShell><p>Nothing here.</p></AppShell>,
  component: ProgramsPage,
});

const verdict = {
  edge: { label: "Shows an edge", cls: "bg-profit/10 text-profit" },
  "no-edge": { label: "No edge after costs", cls: "bg-loss/10 text-loss" },
  research: { label: "Still researching", cls: "bg-warn/10 text-warn" },
};

function ProgramsPage() {
  const { data } = useSuspenseQuery(labQuery());
  const { bots, programs } = data;
  const display = useBotName();
  return (
    <AppShell>
      <h1 className="text-3xl font-semibold tracking-tight">Programs</h1>
      <p className="mt-1 mb-4 text-muted-foreground">Each program is a frozen, pre-registered experiment. Verdicts come from the backtests and studies in the repository.</p>
      <SourceBanner data={data} />
      <div className="grid gap-4 md:grid-cols-2">
        {programs.map((p) => {
          const pb = bots.filter((b) => b.programId === p.id);
          const avg = pb.length ? pb.reduce((s, b) => s + b.returnPct, 0) / pb.length : 0;
          const best = [...pb].sort((a, b) => b.returnPct - a.returnPct)[0];
          const v = verdict[p.verdict];
          return (
            <div key={p.id} className="panel p-5">
              <div className="flex items-start justify-between gap-3">
                <h2 className="text-lg font-medium">{p.name}</h2>
                <span className={cn("shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium", v.cls)}>{v.label}</span>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">{p.description}</p>
              <div className="mt-4 grid grid-cols-3 gap-3 text-sm">
                <div><div className="text-xs text-muted-foreground">Market</div>{p.market}</div>
                <div><div className="text-xs text-muted-foreground">Timeframe</div>{p.timeframe}</div>
                <div><div className="text-xs text-muted-foreground">Avg return</div><Pct value={avg} /></div>
              </div>
              <div className="mt-4 border-t pt-3 text-sm">
                <span className="text-muted-foreground">{pb.length} bots · best: </span>
                {best && (
                  <>
                    <Link to="/bots/$botId" params={{ botId: best.id }} className="text-primary hover:underline">{display(best)}</Link>{" "}
                    <Pct value={best.returnPct} />
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </AppShell>
  );
}
