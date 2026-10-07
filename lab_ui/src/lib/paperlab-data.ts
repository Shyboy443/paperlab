// Types and static program notes for the lab dashboard. Live numbers come from src/lib/paperlab-api.ts.

export type BotStatus = "running" | "paused" | "eliminated" | "frozen";

export interface Program {
  id: string;
  name: string;
  description: string;
  market: string;
  timeframe: string;
  verdict: "edge" | "no-edge" | "research";
}

export interface Trade {
  id: string;
  time: string;
  symbol: string;
  side: "long" | "short";
  kind: string;
  price: number;
  fee: number;
  pnl: number;
  pnlUnit: "pct" | "usd";
}

export interface Bot {
  id: string;
  key: string;
  name: string;
  champion: string;
  persona: string;
  programId: string;
  strategy: string;
  symbol: string;
  status: BotStatus;
  variant: "control" | "jev" | "ladder";
  stage?: string;
  timeframe?: string;
  openPositions?: number;
  returnPct: number;
  grossPct: number;
  winRate: number;
  trades: number;
  maxDrawdown: number;
  sharpe: number;
  feesPct: number;
  equity: { day: number; value: number }[];
}

// Verdicts follow the studies in docs/ (2-year backtest, V9/V11/V12/V13 studies): only V6.2 and V6.6 made money
// after costs over two years.
export const programs: Program[] = [
  { id: "v6", name: "V6 Forward Arena", description: "Hourly and 4-hour strategies that read Bybit positioning: funding, open interest, premium. V6.2 and V6.6 are the only families profitable over the 2-year backtest.", market: "Bybit perps", timeframe: "1h / 4h", verdict: "edge" },
  { id: "v7", name: "V7 Active Challenger", description: "15-minute trend and positioning strategies.", market: "Bybit perps", timeframe: "15m", verdict: "no-edge" },
  { id: "v8", name: "V8.3 Scalpers", description: "5-minute VWAP snap-back with control, Jev and ladder twins.", market: "Bybit perps", timeframe: "5m", verdict: "no-edge" },
  { id: "v9", name: "V9 Stocks", description: "V8 scalp families on US stocks and ETFs, regular sessions only.", market: "Alpaca IEX", timeframe: "5m", verdict: "no-edge" },
  { id: "v11", name: "V11 Scanners", description: "Four scanners over 30 coins with a 25/50/25 take-profit ladder.", market: "Bybit perps", timeframe: "15m", verdict: "no-edge" },
  { id: "v12", name: "V12 Bizzy", description: "Daily breakout ported from Bizzy Bee. The backtest loses after Bybit costs.", market: "ETH, SOL, HYPE", timeframe: "1d", verdict: "no-edge" },
  { id: "v13", name: "V13 Bounce", description: "Maker snap-back to the 24-hour VWAP. The study failed on its test window.", market: "29 coins", timeframe: "15m", verdict: "no-edge" },
  { id: "v14", name: "V14 HTF", description: "Copies of the leading bots that only trade with the 4h and daily trend.", market: "Mixed", timeframe: "5m / 15m", verdict: "research" },
  { id: "video", name: "Video breakout", description: "BTC 4-hour breakout taken from a trading video.", market: "BTC", timeframe: "4h", verdict: "research" },
];

export function labEquity(list: Bot[]) {
  if (!list.length) return [{ day: 0, value: 100 }, { day: 1, value: 100 }];
  const N = 60;
  // Resample each curve to N points so curves of different lengths can be averaged.
  const at = (eq: Bot["equity"], i: number) => eq[Math.round((i / (N - 1)) * (eq.length - 1))]!.value;
  return Array.from({ length: N }, (_, day) => ({
    day,
    value: +(list.reduce((s, b) => s + at(b.equity, day), 0) / list.length).toFixed(2),
  }));
}

export const fmtPct = (n: number) => `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
