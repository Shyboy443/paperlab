# PREREG - r4_08_volregime: volatility compression -> expansion, HTF direction, multi-day holds

Written 2026-10-23 after the DEV scan (2021-01..2024-10) and BEFORE any TEST bar (2024-11-01..2026-10-21) was simulated.
Not to be edited after the TEST run. Code: `engine.py` (rules), `dev_scan.py` (DEV grid + selection rule), `run.py` (everything).

## How this differs from round-1 agent 06 (intraday breakouts, FAIL)
06 traded 15m session ORBs / BB-in-KC squeezes on 6 hand-picked coins with <=24h holds. Here: 4h bars, a mandatory
compression precondition measured against the coin's own 30-day history, direction from a 50-day EMA, trades held
up to 7 days with a wide 4-ATR chandelier, and a point-in-time top-60 universe of ALL USDT perps incl. delisted coins.

## Data / universe
- 4h bars aggregated from agent 07's data.binance.vision 1h archive cache (683 crypto USDT perps, 161 delisted/settling;
  TradFi and index contracts dropped as in r2_rotation). Funding from the same archive (coins without history: +0.01%/8h).
- Universe: top-60 by trailing 30-day quote volume known at the bar's open (>=80% bars present, listed >=30 days),
  refreshed daily. Delisted coins: open positions are closed at their last archived close.

## Rule (selected config `atrp_q0.05_htf_k4.0_H42_both_u60`)
- Squeeze: ATR14(4h)/close is in the bottom 5% of its own trailing 180-bar (30-day) values (pct rank <= 0.05) at some
  bar within the last A=6 bars (24h).
- Trigger at a 4h close: close > highest high of the prior D=20 bars (long) / close < lowest low (short).
- HTF direction: long only if close > EMA(300 x 4h = 50 days) and that EMA is higher than 6 bars ago; short mirrored.
- Entry at the next 4h open (taker). Initial stop = entry -/+ k=4 x ATR14; chandelier trail = extreme since entry -/+ 4 x ATR14
  (ratchets only), stop fills at the stop or the gapped open; time exit at the close of the 42nd bar (7 days).
- Sizing: equal risk rp = 1% of equity per trade at 1x (notional = rp x L x equity / stop distance), max 25% x L of equity
  per position, total gross <= L x equity, <= 1% of the signal bar quote volume, max M=8 positions; when more signals than
  free slots, the most liquid coins (30-day volume rank) are taken first.
- Costs 0.05% fee + 0.02% slippage per side, funding every 4h bar; doubled-cost rerun. Liquidation (L>1): position loses
  its margin (notional/L) when the adverse move from entry reaches 1/L - 0.5%, checked on bar highs/lows.

## DEV scan and selection (dev_scan.py, dev_scan.json)
192 configs: sq {bbw, atrp, range6} x q {0.05, 0.15} x dir {htf, bar} x k {2.5, 4} x H {42, 180} x side {both, long}
x univ {30, 60}. Rule declared before the scan: >=200 DEV trades, net > 0 in both DEV halves (2021-22, 2023-24/10) and
net > 0 at doubled costs; pick the highest DEV geometric mean month. 37/192 eligible, 91/192 net positive.
Selected DEV (1x): mean month +3.30%, median +3.30%, geo +2.96%, max DD 30.6%, 1,297 trades (647 L / 650 S).
DEV-only diagnostics done before TEST: random-coin null (40 draws) DEV net +282% vs random median +63%, p95 +139%.
DEV leverage sweep: mean month 1x 3.3, 2x 6.6, 3x 9.7, 5x 15.9, 6x 19.2, **7x 22.0** (max DD 94%, 38 liquidations), 10x 29.8 (DD 99.6%).

## Attempts (all reported)
1. **A1_1x (primary, decides ROBUST/NOT ROBUST and PASS/FAIL)**: selected config at 1x.
2. A2_7x: same at L=7, the smallest leverage whose DEV mean month >= 20% (shows what chasing the target does).
- Descriptive (not attempts): TEST/full leverage table 1..10x of A1.

## Check 5 neighbour grid (each varied alone, others at the selected value; 36 neighbours)
q {0.025, 0.0375, 0.0625, 0.075}; k {2, 3, 5, 6}; H {21, 32, 52, 63}; univ {30, 45, 75, 90}; W {90, 135, 225, 270};
A {3, 4, 8, 9}; D {10, 15, 25, 30}; E {150, 225, 375, 450}; M {4, 6, 10, 12}.
Pass if >= 70% have net > 0 on both DEV and TEST at baseline costs.

## Check 6 null
300 random versions: same signal bar times, same number of long and short candidates per bar, same sides, sizing, slots and
exits; coins drawn at random from that bar's eligible top-60 universe (seeds 0..299). Pass if the strategy's 2021-01..2026-10
net beats >= 95% of them.

## Other checks: exactly as ROBUSTNESS.md (1x, months 2021-01..2026-10 = 70, bar stamped at its open time).
Check 7 removes the 5 coins with the largest total P&L from the universe and reruns; and largest single trade <= 10% of net profit.

## PASS rule (protocol)
A1 TEST mean month >= +20% at baseline costs AND > 0 at doubled costs AND not wiped AND no trade > 50% of profit.
Expected: FAIL on the 20% target at 1x (DEV mean +3.3%/month).
