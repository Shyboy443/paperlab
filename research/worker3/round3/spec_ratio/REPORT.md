# spec_ratio (Worker 3, round 3): perp/spot volume ratio. Clean FAIL, 0/8

## Idea
Measure how speculative each coin's trading is: Binance perp quote volume divided by Binance spot quote volume (all
USD-stable spot quotes). Then trade it cross-sectionally, long/short, at 1x with a weekly rebalance.

There are two competing stories:
- **Overpricing** (high ratio underperforms). This is the crypto analogue of Johnson & So (2012, JFE), where a high
  option/stock volume ratio predicts low returns. Practitioners make the same point when they say perp-led rallies
  fade (Kaiko 2023, CryptoQuant 2026).
- **Speculative demand / momentum** (high ratio outperforms). He, Manela et al.'s "Fundamentals of Perpetual Futures"
  supports it, as does the evidence that futures volume predicts futures returns.

I found no published crypto test of the volume ratio itself. All the support is by analogy. Sources are in PREREG.md.

## Data
- **Spot klines:** 1,148 spot pairs, including delisted ones, for the 407 of 531 ever-top-60 perps that have a spot
  market. Source is data.binance.vision, cached in `data/w3r3_spec_ratio/`.
- **Name check:** 5 symbols are a different asset on spot than on perps and were dropped.
- **Perp volume:** the survivorship-free daily panel. Engine and costs are unchanged: 0.05% fee + 0.02% slippage per
  side, funding included, 1x.

## DEV (48 configs) and selection
No DEV config reached t ≥ 2. The best was chg14 with sign −1 (t 1.50).

The literature's "overpricing" direction lost on every row. The pre-registered rule (sign with the higher median t,
then the best t inside it) picked:

**lvl, L = 7, sign +1 (long the 10 highest ratios, short the 10 lowest), weekly, top-40.**

| 1x | median | geo | mean | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV 2021-01..2024-10 | −0.01% | +0.58% | +0.90% | 23/47 | 33.1% | 0.75 | 22.4 |
| TEST 2024-11..2026-10 (run once) | −1.80% | −1.61% | −1.34% | 9/24 | 47.9% | −0.90 | 25.3 |
| Full 2021-01..2026-10 | −0.39% | −0.16% | +0.15% | 32/70 | 55.3% | 0.16 | 23.5 |
| TEST, 2x costs | −1.95% | −1.80% | −1.53% | 9/24 | 49.0% | −1.03 | |

- **2026:** YTD +9.7%; October 2026 −6.1%.
- **2x costs, 2025-01..2026-10:** +9.4%. That run starts on a different weekly rebalance day; the same months inside
  the TEST run lose about 24%. Such a large gap from the rebalance day alone shows the result is noise.

## Checks: 0/8
1. **Both halves: FAIL.** TEST net −32.2% (−35.3% at 2x costs).
2. **Most years: FAIL.** Positive in only 3 of 6 years (2022, 2023, 2026). Net since 2022: −2.7%.
3. **Statistically real: FAIL.** t 0.16; bootstrap 5th percentile −1.40.
4. **Not a few months: FAIL.** Without the 3 best months: −49.6%.
5. **Not a knife edge: FAIL.** Only 6% of the 16 neighbours are positive on both DEV and TEST.
6. **Beats chance: FAIL.** Beats 82% of 500 random weekly books, below the 95% bar.
7. **Not a few coins: FAIL.** Net is negative even before removing the top-5 coins.
8. **Compounds: FAIL.** Geo month −0.16%.

## Fit with the running bots and 500 USDT
- **Correlation:** monthly correlation with Worker 1 is −0.18 on TEST; with Worker 2 it is +0.18.
- **As a third leg:** an equal-thirds W1 + W2 + X mix gets worse than the 50/50 W1 + W2 mix. Median falls from
  +3.54% to +2.24%, geo from +4.48% to +2.55%, and months up from 20/24 to 17/24.
- **500 USDT:** works mechanically. That is 20 legs of 25 USDT each; BTC legs (about 2.5%) fall below BTC's
  100 USDT minimum order.

## Verdict
**FAIL against bar (a)-(d). Not PORTFOLIO-ONLY.**

Speculation intensity measured by perp/spot volume has no stable cross-sectional sign on Binance in 2021-26. Neither
direction had t ≥ 2 on DEV, and the selected config lost on TEST. Configs looked at: 48 DEV, plus 16 neighbours run
only for check 5.
