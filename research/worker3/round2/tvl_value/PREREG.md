# tvl_value: pre-registration (written 2026-10-26 before any TEST run)

## Idea and sources
Fundamental on-chain value/growth factors from free DefiLlama data, mapped to Binance USD-M perps.
- Soiman, Dumas & Jimenez-Garces, "What drives DeFi market returns?" (IRFA 2023; arXiv 2204.00251): TVL/market cap as a
  DeFi valuation ratio; they find lagged TVL/MC has weak predictive power (prior: weak).
- Soiman & Mourey, "Pricing DeFi tokens with the Fama-French 3 Factor Model" (HAL hal-04507930, 2024): TVL-to-market as the
  book-to-market analogue; no factor priced after cross-sectional correlation.
- Liu, Tsyvinski & Wu, "Common Risk Factors in Cryptocurrency" (J. Finance 2022; NBER w25882): market, size, momentum.
- Greenfield Capital "DeFi Fundamentals Report" (Nov 2025): TVL / fees / revenue gained predictive power in 2024-25 at
  6-month horizons. Brigida (Cointelegraph, commissioned, 2025): weekly TVL/MC quintiles do not reliably predict.
Honest prior from the literature: weak. A clean FAIL is expected to be likely.

## Data (cached in data/w3r2_tvl_value/)
- DefiLlama `/protocols`, `/protocol/<slug>` (parent slugs aggregate children), `/v2/chains`, `/v2/historicalChainTvl/<chain>`,
  `/summary/fees/<slug>?dataType=dailyFees|dailyRevenue` (all free; no 402 met on these routes).
- Mapping: DefiLlama token symbol == Binance perp base (incl. 1000x prefixes) AND the gecko_id price must match the Binance
  close within 0.8-1.25x (live coins, current price) or 0.67-1.5x (delisted coins, DefiLlama historical price 3 days before
  the last traded Binance bar). Chains preferred over protocols for the same symbol (L1/L2 gas tokens). Manual alias
  MATIC -> Polygon. LUNA (classic) could not be mapped (panel column is LUNA2 after 2022) -> survivorship gap noted.
  286 coins mapped; 20-130 eligible per day (grows over time).
- Point-in-time: the value used at the open of day i is the last DefiLlama point stamped <= t[i] - 1 day; fees are 30-day
  trailing sums lagged the same way. Caveat: DefiLlama histories are today's vintage (adapters re-run), not as-published.
- Market cap: no free full-history source for all coins (CoinGecko free = 365 days; CoinMetrics community covers ~25 of the
  mapped coins). So the level ratios TVL/MC and P/F are NOT tested. Instead the CHANGE of the ratio is used:
  vchg = dlog TVL - dlog price over L days = dlog(TVL/MC) under the approximation that circulating supply is flat over L.
  (Supply inflation makes MC grow faster than price, so vchg slightly overstates value improvement for inflating tokens.)

## DEV exploration (2021-01..2024-10 only; dev_grid.json, dev_nbr.json, tranche scan in the log)
Grid: 5 scores (tvlg, vchg, feeg, revg, fychg) x L {14,30,60,90} x {LS, long-only, long-only + BTC>SMA100 gate} x rebal
{7,30} x k=5, plus reversed-sign LS (all reversed versions lose -> the growth direction is the right sign).
Best LS: vchg L30 k5 monthly (DEV geo +5.98%, median +3.11%, DD 23%), BUT monthly rebalancing showed strong PHASE luck
(start offsets 0..25 days: geo +5.98, +3.43, +4.15, +2.55, +2.74, +1.92). To remove it the selected config uses 4
staggered tranches. Long-only versions have 60-90% drawdowns (rejected).

## The ONE selected config
- Score: vchg, L = 30 days (log TVL growth minus log price change, both over 30 days, known at the open).
- Eligible: point-in-time top-100 perps by 30-day quote volume AND mapped TVL >= 20 M USD.
- Long the 5 highest, short the 5 lowest, equal weight, 1x gross (50% long / 50% short).
- 4 staggered sub-books (25% capital each), each rebalanced every 28 days, phases 7 days apart (= a quarter of the book
  re-formed every week). Engine: engine_r2.run unchanged (next-open fills, fee 0.05% + slip 0.02% per side, funding,
  1% of previous-day volume cap). DEV tranche result: geo +4.07%, median +1.43%, mean +4.50%, 36/47 up, DD 28.7%, t 3.07.

## Check 5 neighbour grid (each at baseline cost, DEV and TEST)
L {15, 22, 38, 45}; k {3, 4, 6, 8}; universe {50, 75, 125, 150}; min TVL {10, 15, 25, 30} M; rebalance schemes
{14d x 2 tranches, 56d x 8 tranches, 7d x 1, 28d x 1 (single book)}. 20 neighbours, pass if >= 70% positive on both.

## Checks 6 and 7
6: 200 random versions: random scores on the same eligible set, same 5/5, same 4 tranches, same costs (full period).
7: remove the 5 coins with most profit (full period) and rerun; largest single position share of total profit <= 10%.

TEST (2024-11-01..2026-10-21) is run ONCE by run.py after this file is written.
