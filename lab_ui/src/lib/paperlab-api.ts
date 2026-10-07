import { queryOptions } from "@tanstack/react-query";
import { withChampions } from "./champions";
import { programs as PROGRAMS, type Bot, type BotStatus, type Program, type Trade } from "./paperlab-data";

// The page is served by PaperLab itself (/lab), so it reads the public, read-only API on the same origin. In
// `npm run dev` Vite proxies these paths to Railway (vite.config.ts). Nothing here sends credentials.
const API = "/api/public/competition";
const PROGRAM_IDS = ["v6", "v7", "v8", "v9", "v11", "v12", "v13", "v14", "video"] as const;
const REFRESH_MS = 15_000;

export interface LabData {
  source: "live" | "error";
  error?: string | undefined;
  asOf: number;
  bots: Bot[];
  programs: Program[];
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = any;

async function getJson(path: string): Promise<Json> {
  const res = await fetch(path, { headers: { Accept: "application/json" }, cache: "no-store", signal: AbortSignal.timeout(20_000) });
  if (!res.ok) throw new Error(`PaperLab API ${res.status}`);
  return res.json();
}

const num = (v: unknown, d = 0) => (typeof v === "number" && Number.isFinite(v) ? v : Number(v) || d);

function toEquity(curve: unknown, start: number): Bot["equity"] {
  const vals = Array.isArray(curve)
    ? curve.map((p: Json) => (Array.isArray(p) ? num(p[1]) : num(p?.equity ?? p))).filter((v) => v > 0)
    : [];
  const base = start > 0 ? start : vals[0] ?? 1;
  if (vals.length < 2) return [{ day: 0, value: 100 }, { day: 1, value: +(((vals[0] ?? base) / base) * 100).toFixed(2) }];
  return vals.map((v, day) => ({ day, value: +((v / base) * 100).toFixed(2) }));
}

function toStatus(row: Json, programLive: boolean): BotStatus {
  if (row.error) return "frozen";
  const st = String(row.program_status ?? "").toUpperCase();
  if (st === "ELIMINATED" || st === "RETIRED" || st === "HALTED") return "eliminated";
  if (row.live === false || !programLive) return "paused";
  return "running";
}

function toBot(program: string, row: Json, names: Json, programLive: boolean): Omit<Bot, "champion"> {
  const key = String(row.key);
  const start = num(row.start_equity, 20) || 20;
  const n = names?.[`${program}|${key}`] ?? {};
  const costs = num(row.fees) + num(row.slippage) + num(row.funding_paid);
  return {
    id: `${program}--${key}`,
    key,
    name: String(n.name ?? key),
    persona: String(n.persona ?? row.family ?? "").replace(/_/g, " ").toLowerCase(),
    programId: program,
    strategy: String(row.strategy_id ?? key),
    symbol: String(row.symbol || row.coin || (Array.isArray(row.coins_traded) ? "multi-coin" : "—")),
    status: toStatus(row, programLive),
    stage: String(row.program_status ?? row.maturity ?? ""),
    timeframe: String(row.timeframe ?? ""),
    openPositions: Array.isArray(row.open_positions) ? row.open_positions.length : 0,
    variant: String(row.role ?? "").toUpperCase().includes("JEV") || key.includes("+JEV") ? "jev" : /ladder|TP/i.test(key) ? "ladder" : "control",
    returnPct: +(num(row.return_pct) * 100).toFixed(2),
    grossPct: +((num(row.gross) / start) * 100).toFixed(2),
    winRate: +(num(row.win_rate) * 100).toFixed(1),
    trades: num(row.trades),
    maxDrawdown: +(num(row.max_dd) * 100).toFixed(2),
    sharpe: +num(row.expectancy_r).toFixed(2),
    feesPct: +((costs / start) * 100).toFixed(2),
    equity: toEquity(row.curve, start),
  };
}

function programInfo(id: string): Program {
  return PROGRAMS.find((p) => p.id === id) ?? { id, name: id.toUpperCase(), description: "", market: "—", timeframe: "—", verdict: "research" };
}

export async function getLabData(): Promise<LabData> {
  try {
    const [roster, ...results] = await Promise.all([
      getJson(`${API}/roster`).catch(() => null),
      ...PROGRAM_IDS.map((p) => getJson(`${API}/${p}`).catch(() => null)),
    ]);
    const names = roster?.names ?? {};
    const bots: Omit<Bot, "champion">[] = [];
    const programs: Program[] = [];
    PROGRAM_IDS.forEach((p, i) => {
      const payload = results[i];
      const rows: Json[] = payload?.leaderboard ?? [];
      if (!rows.length) return;
      const live = String(payload?.status?.status ?? "").toUpperCase() === "LIVE";
      programs.push(programInfo(p));
      for (const r of rows) if (r?.key) bots.push(toBot(p, r, names, live));
    });
    if (!bots.length) return { source: "error", error: "PaperLab returned no bots.", asOf: Date.now(), bots: [], programs: PROGRAMS };
    return { source: "live", asOf: num(roster?.as_of_ms, Date.now()), bots: withChampions(bots), programs };
  } catch (e) {
    return { source: "error", error: e instanceof Error ? e.message : "Could not reach PaperLab.", asOf: Date.now(), bots: [], programs: PROGRAMS };
  }
}

export function parseBotId(id: string): { program: string; key: string } | null {
  const m = /^(v\d+|video)--([\w.+\-]{1,120})$/.exec(id);
  return m ? { program: m[1]!, key: m[2]! } : null;
}

export async function getBotTrades(id: string): Promise<Trade[] | null> {
  const ref = parseBotId(id);
  if (!ref) return null;
  try {
    const res = await getJson(`${API}/${ref.program}/bot/${encodeURIComponent(ref.key)}`);
    const rows: Json[] = res.trades ?? [];
    const start = num(res.bot?.start_equity, 20) || 20;
    return rows
      .slice(-50)
      .reverse()
      .map((t, i) => ({
        id: `${id}-${i}`,
        time: new Date(num(t.exit_ts ?? t.entry_ts)).toISOString(),
        symbol: String(t.symbol ?? "—"),
        side: String(t.side).toLowerCase() === "short" ? "short" : "long",
        kind: String(t.exit_kind ?? "close"),
        price: +num(t.exit_price ?? t.entry_price).toPrecision(6),
        fee: +((num(t.fees) / start) * 100).toFixed(3),
        pnl: +((num(t.net) / start) * 100).toFixed(2),
        pnlUnit: "pct",
      }) satisfies Trade);
  } catch {
    return null;
  }
}

export const labQuery = () =>
  queryOptions({ queryKey: ["paperlab", "lab"], queryFn: getLabData, staleTime: REFRESH_MS, refetchInterval: REFRESH_MS });

export const tradesQuery = (id: string) =>
  queryOptions({ queryKey: ["paperlab", "trades", id], queryFn: () => getBotTrades(id), staleTime: REFRESH_MS, refetchInterval: REFRESH_MS });

// ---- Roster (home feed), health, candles: all public GET endpoints ----

export interface RosterBot {
  id: string;
  name: string;
  persona: string;
  program: string;
  programLabel: string;
  where: string;
  equity: number;
  net: number;
  returnPct: number;
  trades: number;
  winRate: number;
  profitFactor: number;
  maxDrawdown: number;
  positions: { symbol: string; side: string; entry: number; mark: number; upnl: number }[];
  equityCurve: Bot["equity"];
  backtest: { returnPct: number; trades: number; profitFactor: number; maxDrawdown: number } | null;
}
export interface Idea { ts: number; program: string; symbol: string; side: string; bots: { name: string; verdict: string; reason: string; net: number | null }[] }
export interface RosterData {
  ok: boolean;
  error?: string | undefined;
  kpis: { allNet: number; allTrades: number; inProfit: number; positionsOpen: number; botsTotal: number };
  onStage: RosterBot[];
  ready: RosterBot[];
  scanners: RosterBot[];
  ideas: Idea[];
  rule: string;
}

function toRosterBot(r: Json): RosterBot {
  const start = num(r.start_equity, 20) || 20;
  const bt = r.backtest;
  return {
    id: `${r.program}--${r.key}`,
    name: String(r.name ?? r.key),
    persona: String(r.persona ?? ""),
    program: String(r.program ?? ""),
    programLabel: String(r.program_label ?? r.program ?? ""),
    where: String(r.where ?? ""),
    equity: num(r.equity),
    net: num(r.net),
    returnPct: +(num(r.return) * 100).toFixed(2),
    trades: num(r.trades),
    winRate: +(num(r.win_rate) * 100).toFixed(1),
    profitFactor: +num(r.profit_factor).toFixed(2),
    maxDrawdown: +(num(r.max_dd) * 100).toFixed(2),
    positions: (Array.isArray(r.positions) ? r.positions : []).map((p: Json) => ({
      symbol: String(p.symbol ?? ""), side: String(p.side ?? ""), entry: num(p.entry), mark: num(p.mark), upnl: num(p.upnl),
    })),
    equityCurve: toEquity(r.curve, start),
    backtest: bt ? { returnPct: num(bt.return_pct), trades: num(bt.trades), profitFactor: num(bt.profit_factor), maxDrawdown: num(bt.max_dd_pct) } : null,
  };
}

const emptyRoster = (error: string): RosterData => ({
  ok: false, error, kpis: { allNet: 0, allTrades: 0, inProfit: 0, positionsOpen: 0, botsTotal: 0 }, onStage: [], ready: [], scanners: [], ideas: [], rule: "",
});

export async function getRoster(): Promise<RosterData> {
  try {
    const d = await getJson(`${API}/roster`);
    const k = d.kpis ?? {};
    return {
      ok: true,
      kpis: { allNet: num(k.all_net), allTrades: num(k.all_trades), inProfit: num(k.in_profit), positionsOpen: num(k.positions_open), botsTotal: num(k.bots_total) },
      onStage: (d.roster ?? []).map(toRosterBot),
      ready: (d.ready ?? []).map(toRosterBot),
      scanners: (d.scanners ?? []).map(toRosterBot),
      ideas: (d.ideas ?? []).slice(0, 12).map((i: Json) => ({
        ts: num(i.last_ts ?? i.ts), program: String(i.program ?? ""), symbol: String(i.symbol ?? ""), side: String(i.side ?? ""),
        bots: (i.bots ?? []).map((b: Json) => ({ name: String(b.name ?? b.key), verdict: String(b.verdict ?? ""), reason: String(b.reason ?? ""), net: b.net == null ? null : num(b.net) })),
      })),
      rule: String(d.rule?.text ?? ""),
    };
  } catch (e) {
    return emptyRoster(e instanceof Error ? e.message : "Could not reach PaperLab.");
  }
}

export interface Health { ok: boolean; programs: { id: string; status: string; problem: string | null }[] }

export async function getHealth(): Promise<Health> {
  try {
    const res = await fetch("/public/health/deep", { headers: { Accept: "application/json" }, cache: "no-store", signal: AbortSignal.timeout(15_000) });
    const d: Json = await res.json().catch(() => ({}));
    const programs = Object.entries(d.programs ?? {}).map(([id, p]: [string, Json]) => ({ id, status: String(p?.status ?? "?"), problem: p?.problem ? String(p.problem) : null }));
    return { ok: res.status === 200, programs };
  } catch {
    return { ok: false, programs: [] };
  }
}

export interface Candles { candles: { ts: number; open: number; high: number; low: number; close: number }[]; markers: { ts: number; kind: string; side: string; price: number; botKey: string }[] }

export async function getCandles(program: string, symbol: string, tf: string): Promise<Candles | null> {
  if (!/^(v\d+|video)$/.test(program) || !/^[A-Z0-9]{2,20}$/.test(symbol) || !/^\d{1,2}[mhd]$/.test(tf)) return null;
  try {
    const d = await getJson(`${API}/${program}/candles?symbol=${symbol}&tf=${tf}`);
    return {
      candles: (d.candles ?? []).map((c: Json) => ({ ts: num(c[0]), open: num(c[1]), high: num(c[2]), low: num(c[3]), close: num(c[4]) })),
      markers: (d.markers ?? []).map((m: Json) => ({ ts: num(m.ts), kind: String(m.kind), side: String(m.side), price: num(m.price), botKey: String(m.bot_key ?? "") })),
    };
  } catch {
    return null;
  }
}

export const rosterQuery = () => queryOptions({ queryKey: ["paperlab", "roster"], queryFn: getRoster, staleTime: REFRESH_MS, refetchInterval: REFRESH_MS });
export const healthQuery = () => queryOptions({ queryKey: ["paperlab", "health"], queryFn: getHealth, staleTime: REFRESH_MS, refetchInterval: REFRESH_MS });
export const candlesQuery = (program: string, symbol: string, tf: string) =>
  queryOptions({ queryKey: ["paperlab", "candles", program, symbol, tf], queryFn: () => getCandles(program, symbol, tf), staleTime: REFRESH_MS, refetchInterval: REFRESH_MS });
