import { Link, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { healthQuery } from "@/lib/paperlab-api";
import { setNameMode, useNameMode } from "@/lib/names";
import { Bot, FlaskConical, LayoutDashboard, ShieldCheck, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

const nav = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/bots", label: "Bots", icon: Bot },
  { to: "/programs", label: "Programs", icon: FlaskConical },
  { to: "/analyze", label: "Cost analyzer", icon: Sparkles },
] as const;

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-sidebar p-5 md:flex">
        <Link to="/" className="mb-8 flex items-center gap-2">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-primary text-primary-foreground">
            <FlaskConical className="h-4 w-4" />
          </div>
          <span className="text-lg font-semibold tracking-tight">PaperLab</span>
        </Link>
        <nav className="flex flex-col gap-1">
          {nav.map(({ to, label, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              activeOptions={{ exact: to === "/" }}
              className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
              activeProps={{ className: "!bg-sidebar-accent !text-foreground" }}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          ))}
          {/* The operator console (GO LIVE, mirrors, kill switch) stays on the password-protected classic page. */}
          <a href="/" className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground">
            <ShieldCheck className="h-4 w-4" />
            Operator console
          </a>
        </nav>
        <div className="mt-auto space-y-3">
          <NameToggle />
          <div className="rounded-lg border p-3 text-xs text-muted-foreground">
            <HealthBadge />
            Paper trading on live prices. Read-only. Not financial advice.
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 border-b bg-background/80 px-4 py-3 backdrop-blur md:hidden">
          <div className="flex items-center justify-between">
            <span className="font-semibold">PaperLab</span>
            <NameToggle compact />
          </div>
          <nav className="mt-2 flex gap-4 overflow-x-auto text-sm">
            {nav.map(({ to, label }) => (
              <Link key={to} to={to} activeOptions={{ exact: to === "/" }} className="shrink-0 text-muted-foreground" activeProps={{ className: "!text-primary" }}>
                {label}
              </Link>
            ))}
            <a href="/" className="shrink-0 text-muted-foreground">Console</a>
          </nav>
        </header>
        <main key={pathname} className="page-in mx-auto w-full max-w-7xl flex-1 p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}

function NameToggle({ compact = false }: { compact?: boolean }) {
  const mode = useNameMode();
  const btn = (active: boolean) =>
    cn("rounded-md px-2 py-1 transition-colors", active ? "bg-sidebar-accent text-foreground" : "text-muted-foreground hover:text-foreground");
  return (
    <div className={cn("text-xs", !compact && "rounded-lg border p-2")}>
      {!compact && <div className="mb-1 px-1 text-muted-foreground">Bot names</div>}
      <div className="flex gap-1" role="group" aria-label="Bot names">
        <button type="button" className={btn(mode === "paperlab")} aria-pressed={mode === "paperlab"} onClick={() => setNameMode("paperlab")}>PaperLab</button>
        <button type="button" className={btn(mode === "champion")} aria-pressed={mode === "champion"} onClick={() => setNameMode("champion")}>Champions</button>
      </div>
    </div>
  );
}

function HealthBadge() {
  const { data } = useQuery(healthQuery());
  // MARKET_CLOSED is the stock program waiting for the US session: normal, not a problem.
  const problems = data?.programs.filter((p) => p.problem || !["LIVE", "WARMING_UP", "MARKET_CLOSED"].includes(p.status)) ?? [];
  const label = !data ? "Checking system…" : data.ok && problems.length === 0 ? "All systems live" : data.ok ? `${problems.length} program issue${problems.length > 1 ? "s" : ""}` : "System problem";
  return (
    <div className="mb-1 flex items-center gap-2 text-foreground" title={data?.programs.map((p) => `${p.id}: ${p.status}${p.problem ? ` (${p.problem})` : ""}`).join("\n")}>
      <span className={data && (!data.ok || problems.length) ? "h-2 w-2 rounded-full bg-loss" : "live-dot"} /> {label}
    </div>
  );
}
