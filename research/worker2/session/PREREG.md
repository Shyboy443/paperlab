# PREREG: Worker 2 / session — concentration fix for r4_09 (written 2026-10-25, before any variant was run)

## Honest status of the data
- The candidate is r4_09_session (IM ALT H=16 k=2.0 X=26 K=5 W=480, mom). It was pre-registered and run on TEST once
  by the r4 round. TEST (2024-11-01..2026-10-21) has therefore **already been seen once**, including the check-7
  failure and the names of the top-5 profit coins (RAVE, DEXE, POWER, VELVET, 1000PEPE). This is not a clean holdout.
- Before writing this file I ran only `repro.py`: it reproduces r4_09's DEV/TEST/full numbers exactly from its own code
  (repro.json) and computes one DEV-only statistic of the sizing input (no returns): the trailing 480 h hourly vol of
  the coins on DEV signal days. Percentiles: p25 0.834%, **p50 1.154%**, p75 1.551%.
- No variant (no sizing change) has been run on any window before this file.

## Why r4_09 fails check 7 (first principles)
Every position gets the same share of equity (1/max(n, K) = 20%) whatever the coin's volatility. A coin that is
moving >= 2 of ITS OWN vol units is selected the same way whether its hourly vol is 0.6% or 4%, so the wildest coins
(new listings, meme coins) carry 3-6x the risk of a normal alt in the same 20% slot. One +100% night in such a coin
is 20% of equity. That is a sizing flaw, not a signal property. The textbook fix is equal-risk sizing.

## The ONE primary rule (P): volatility-scaled sizes, capped at the old size
- Signal, universe, entry 16:00 UTC, exit 02:00 UTC next day, K=5, costs, funding, 1% volume cap: all unchanged.
- Size of each position = equity / max(n, K) x m_j, with **m_j = min(1, sig_ref / sigma_j)**.
  sigma_j = the coin's std of hourly log returns over the previous 480 h, known at the day's 00:00 UTC open (the same
  number the z-score already uses). **sig_ref = 1.154%** (median over DEV signals, fixed above, never re-fitted).
- A coin with normal or low vol keeps the full slot; a coin with 2x the reference vol gets half a slot. Sizes are only
  ever scaled down, so gross exposure stays <= 1x. The unused part stays in cash (no re-leveraging).

## Neighbours (sensitivity only, not selection)
- N1: sig_ref = 0.834% (DEV p25, stronger scaling).
- N2: sig_ref = 1.551% (DEV p75, weaker scaling).
P is the answer whatever N1/N2 show. No other variants. Nothing is tuned on TEST.

## How it is judged (exactly ROBUSTNESS.md, 1x, same code as r4_09/run.py where possible)
1. DEV and TEST net > 0 at baseline and doubled costs.
2. >= 4 of 6 calendar years positive, and 2022-01..2026-10 positive.
3. t-stat of the 70 monthly means >= 2.0 and block-bootstrap (3-month blocks, 5,000 draws, seed 7) p5 of mean > 0.
4. Net positive after deleting the 3 best months.
5. Same 20-neighbour grid as r4_09 (H 8/12/20/24 with 10 h hold; k 1/1.5/2.5/3; hold 5/8/12/15 h; K 3/4/6/8;
   W 240/360/600/720), each with rule P applied (sig_ref fixed at 1.154%, sigma from the neighbour's own W);
   >= 70% net positive on both DEV and TEST.
6. 200 random versions: same days, same number of longs/shorts per day, same windows/costs, random alts from that
   day's universe, **sized by the same rule P using the random coin's own sigma**. Must beat >= 95%.
7. Remove the 5 coins with the most profit under P from the universe and re-run: must stay net positive; and the
   single largest position must be <= 10% of total profit. Primary metric = **dollar P&L of the position / dollar
   total profit of the full-period curve**, exactly as r4_09 measured it. For information only, also report the
   share of total log-growth (log(1 + pnl/equity)), which removes the compounding/size-of-account effect.
8. Full-period geometric mean month > 0 and >= half the arithmetic mean month.
ROBUST only if all 8 pass. Lead with median and geometric months, not the mean.

## Also reported (no decisions made from them)
- DEV / TEST / full at 1x: median, geo, mean, worst month, max DD, months up, trades; same at doubled costs.
- Year by year; and 2025-01..2026-10 alone at doubled costs (sign).
- Correlation with Worker 1 (r4_07_ensemble): monthly (results.json monthly_1x) and daily (_rbook_full_1x.npy),
  2022-01..2026-10 and full; 50/50 combination rebalanced monthly (and daily) — mean, median, geo, worst, max DD.
- 500 USDT size check (smallest order vs Binance USD-M min notional) and a slippage estimate for 100 USDT market
  orders from public order books (read-only public endpoint, no keys).
Any change after this point is a new, labelled attempt and will be reported as such.
