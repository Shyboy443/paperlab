# Agent 02 - cross-sectional rotation across Binance USDT perps - verdict: **FAIL**

**Idea.** Each day take the 40 most liquid perps (point-in-time `liquid_universe` rule), rank them by past return
(4h..28d), BTC-beta-adjusted return or volume surge, and go long winners / short losers (momentum) or the reverse,
rebalancing every 4h, daily or weekly.

**DEV (2021-01..2024-10), ~500 configs** (logs in `data/monthly20_02_xs_momentum/`): reversal loses at every horizon and
every year; 4h rebalancing is killed by costs; long-only top-1/top-3 momentum (with a BTC>SMA50 gate) gives DEV means of
+22..+38%/mo but almost all from 2021 (2024-Jan..Oct negative in nearly every variant). The only steady family is
long-short top-10/bottom-10 on 14-28 day momentum, rebalanced weekly: ~+2-2.5%/mo, 20-26% DD at 1x.

**Pre-registered TEST (2024-11..2026-10, 24 months), 6 attempts, one run each:**

| attempt | mean/mo | median | worst | geo/mo | max DD | 2x-cost mean |
|---|---|---|---|---|---|---|
| P1 mom7 top3 BTC-gate 2x | +19.05 | -24.75 | -78.8 | -24.9 | 99.98% | +16.9 |
| P2 same 1x | +5.57 | -7.78 | -52.3 | -5.6 | 95.6% | +4.6 |
| P3 mom7 top1 gate 1x | +1.67 | -27.1 | -94.5 | -28.1 | 100% | +0.6 |
| **P4 mom14 long10/short10 weekly 2x** | **+11.81** | **+11.22** | **-19.9** | **+9.5** | **44.9%** | **+11.1** |
| P5 BTC-residual 28d L/S10 weekly 1x | +5.42 | +1.66 | -14.8 | +4.3 | 33.3% | +5.1 |
| P6 mom7 top3 no gate 1x | +5.53 | -16.4 | -64.6 | -11.6 | 98.1% | +3.9 |

P1's +19% mean is an artefact: a few +100% months on a nearly dead account (equity -99.98%).

**P4 TEST months (%):** 24-11 +66.3, 24-12 +26.5, 25-01 +1.7, 25-02 -0.5, 25-03 -19.9, 25-04 +39.6, 25-05 +15.5,
25-06 -6.1, 25-07 +13.4, 25-08 +9.7, 25-09 +2.0, 25-10 +19.6, 25-11 +23.8, 25-12 +61.4, 26-01 +32.8, 26-02 -15.0,
26-03 +16.3, 26-04 -16.8, 26-05 -18.0, 26-06 +40.1, 26-07 -6.4, 26-08 -10.1, 26-09 +12.8, 26-10 -5.2.
15/24 up, net +783%, 1,147 trades, 102 isolated-margin liquidations; biggest single trade = 39% of profit, top 3 ~ all of it.

**Leverage for +20%/month: not reachable.** P4 rules (post-hoc, not pre-registered): TEST mean 1x +4.9, 2x +11.8,
3x +12.9 (peak), 4x +9.6 (DD 79%), 5x +7.8 (DD 94%), 6x +3.5; on DEV 4-5x already means 96-99.7% DD. Extra leverage
turns normal alt swings into liquidations. The long-only family hits a 20% *arithmetic* mean at 3x only while equity goes
to ~0, and is wiped out at 4x on both DEV and TEST.

**Caveats.** Universe = perps still trading today (delisted coins missing; flatters long-momentum). P4's TEST is much
better than its DEV (+2.9%/mo geo) - probably partly luck from 2025's strong alt dispersion (alt bleed vs a few trending
winners). The 4h grid used agent 04's 1h cache, which still lacked 156 of 373 coins.

**Verdict: FAIL.** No pre-registered attempt delivers a real +20% mean month. Most useful finding: weekly 14-day
cross-sectional momentum (long top-10 / short bottom-10, 2x) made ~+9.5%/mo geometric on TEST with a 45% DD and survived
doubled costs - far below target, worth only a forward paper test at 1-2x.
