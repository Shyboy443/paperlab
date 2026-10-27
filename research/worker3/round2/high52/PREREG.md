# PREREG: high52 (Worker 3, round 2). Written 2026-10-26, BEFORE any TEST run.

## Family and sources
Price anchoring / nearness to highs. Investors anchor on a salient past high, so news arriving near the high is
under-reacted to and coins close to their high keep outperforming those far from it.
- George & Hwang (2004), "The 52-Week High and Momentum Investing", J. Finance 59(5) 2145-2176: nearness to the
  52-week high (P / 52w-high) predicts returns better than past returns and does not reverse long-run.
  (summary: https://cxoadvisory.com/technical-trading/the-52-week-high-as-a-momentum-indicator-for-individual-stocks)
- Jia, Simkins, Yan, Zhang & Zhao, "Psychological Anchoring Effect and Cross Section of Cryptocurrency Returns",
  J. Banking & Finance 182 (2026) (https://econpapers.repec.org/RAS/pji332.htm; Quantpedia entry): nearness to the
  52-week high is positively related to future crypto cross-sectional returns, weekly rebalance, 2014-2023.
- Ficura (2023), "Impact of size and volume on cryptocurrency momentum and reversal", FFA WP 5:003
  (https://wp.ffu.vse.cz/artkey/wps-202301-0003_impact-of-size-and-volume-on-cryptocurrency-momentum-and-reversal.php):
  distance from the k-week high predicts POSITIVELY for large/liquid coins (t 4.93), negatively for small/illiquid ones,
  and beats plain momentum. Our universe is the liquid top-40, i.e. the "large" segment.
- Donchian channel breakout (position inside the N-day high/low channel) as the trend-following practitioner form.
Crypto evidence is in-sample (2014-2023); none of it covers 2025-26, which is our TEST.

## Data, engine, costs
Survivorship-free daily panel (`research/monthly20/r2_rotation/data.py`, crypto USDT perps incl. delisted),
point-in-time top-N by 30-day quote volume; agent-02 engine `engine_r2.run` unchanged (signal from bars up to day d-1,
market fill at day d open, fee 0.05% + slip 0.02% per side, funding, 1% bar-volume cap), 1x gross, equal weight per leg.
Code: `lib.py`.

## DEV grid (2021-01..2024-10 only), `dev_grid.py` -> `dev_grid.json` / `dev_grid.log` (192 rows, all kept)
kind {nh = C/max(H,L) (George-Hwang), rng = (C-minL)/(maxH-minL) (Donchian position)} x L {7,14,30,60,90,180,365}
x k {5,10} x legs {LS, LO, LO with BTC>SMA50 gate} x rebal {1,7}, top-40; plus `mom` reference rows (L 14/60).
Findings: long-only anchoring books have DEV max DD 46-89% (gated ~50-65%); George-Hwang `nh` LS is weaker than `rng` LS;
the 52-week (365d) versions are weak in crypto at these horizons; the best anchoring t-stats are rng L30-60 LS.
Plain mom14 LS10 weekly (Worker 1's leg) has DEV t 3.01, still higher than any anchoring config.

## Selection rule (fixed in dev_grid.py before it ran)
Anchoring kinds with DEV geo > 0, DEV net > 0 at doubled cost, DEV daily corr with Worker 1 book < 0.50 and with plain
14d LS10 weekly rotation < 0.70, DEV max DD <= 40%; highest DEV monthly t-stat. (Fallback without the DD filter: not needed.)

## THE ONE selected config
**rng, L = 30, k = 10, LS, rebalance daily, universe top-40, 1x.** Every day at 00:00 UTC, for each coin in the
point-in-time top-40 with >= 27 of the last 30 daily bars: score = (yesterday's close - 30-day low) / (30-day high -
30-day low) using the last 30 completed daily bars. Long the 10 highest (closest to their 30-day high), short the 10 lowest
(closest to their 30-day low), 50% gross per side, market orders at the daily open (only the deltas are traded).
DEV: median +2.16%, geo +3.41%, mean +3.90%, 26/47 up, DD 31.3%, t 2.55, net +383% (2x cost +205%),
DEV daily corr Worker 1 0.416, corr mom14 LS weekly 0.579.
Honest prior: the DEV geo (+3.41%) is already below the bar's +5.33%, so a PASS would need TEST to beat DEV.

## Checks (exactly ROBUSTNESS.md)
- Check 5 neighbour grid (each changed alone, 16 neighbours): L {15, 22, 38, 45}; k {5, 8, 12, 15};
  rebal {2, 3, 5, 7}; univ {20, 30, 50, 60}. Pass if >= 70% net positive on both DEV and TEST at baseline cost.
- Check 6: 500 random books, 10 long / 10 short, daily rebalance, top-40, same costs, random scores redrawn each day.
  Because daily redraws inflate random turnover, a second set of 500 random books whose random scores are held for
  30-day blocks (turnover closer to the strategy's) is also run; check 6 passes only if the strategy beats >= 95% of BOTH.
- Check 7: remove the 5 coins with the largest profit contribution from the universe and rerun; max single position
  share <= 10% of total profit.
- Also: doubled-cost run 2025-01..2026-10, Worker 1/2 monthly correlation, Worker 1 daily correlation, W1+W2+X mix,
  2026 YTD, October 2026, 500 USDT feasibility. TEST is run ONCE by `run.py`.
