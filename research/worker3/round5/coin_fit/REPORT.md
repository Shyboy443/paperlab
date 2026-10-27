# coin_fit (Worker 3 round 5): FAIL. Choosing a coin per strategy does not work

The agent could not write this file, so it was saved from its final message.

PREREG.md was written before TEST and HOLDOUT, and each was run once. Files: `lib.py`, `run.py`, `dev.py`, `dev.json`,
`results.json`, `oos.log`, plus `data/w3r5_coin_fit/dev_snapshot.json`.

## Setup
**Six daily rules with fixed parameters from the literature:**
- TSMOM60 L/S (Moskowitz-Ooi-Pedersen 2012; Liu-Tsyvinski 2021);
- EMA20/100 (Faber 2007; Hurst et al. 2017);
- Donchian 55/20 (Turtles);
- 52-week high (George-Hwang 2004): long while close ≥ 90% of the 365-day high;
- RSI-2 (Connors-Alvarez);
- Bollinger-20 mean reversion.

**Personality measures:** VR(10), Hurst, lag-1 autocorrelation, trend-minus-mean-reversion Sharpe.

**Universe:** the 20 largest perps by December 2020 volume that also have pre-2020 spot data.

**Costs:** 0.07% per side at 1x. TEST uses perps with funding. HOLDOUT uses spot, with shorts assumed possible and no
borrow cost.

**Selection:** each coin gets the rule with the best DEV Sharpe; coins with a Sharpe of 0.5 or less are skipped. This
gives 14 pairs, equal weight:

| Rule | Coins |
|---|---|
| Donchian | BTC, ETH, BNB |
| RSI-2 | XRP, XLM |
| EMA | BCH, ADA, TRX |
| TSMOM | ZIL |
| 52-week high | THETA, ETC, WAVES, VET |
| Bollinger | XMR |

## Results at 1x
| Book | Window | Median | Geo | Months up | Max DD | t | Trades/mo | Net | Net, 2x costs |
|---|---|---|---|---|---|---|---|---|---|
| CHOSEN | DEV (in-sample) | +1.10 | +3.37 | 27/46 | 19.3% | 2.52 | 4.9 | +359% | +343% |
| CHOSEN | TEST | +0.59 | +0.94 | 14/24 | 14.7% | 1.13 | 4.7 | +25% | +23% |
| CHOSEN | HOLDOUT | 0.00 | +1.44 | 16/36 | 44.1% | 1.28 | 3.7 | +67% | +63% |
| All 120 pairs | TEST | +0.27 | +2.20 | 15/24 | 14.3% | 1.61 | 47 | +69% | +65% |
| All 120 pairs | HOLDOUT | +0.63 | +1.24 | 23/36 | 36.2% | 1.19 | 34 | +56% | +50% |

**Each rule on all 20 coins** (net):

| Rule | TEST | HOLDOUT |
|---|---|---|
| TSMOM | +163% | −18% |
| EMA | +67% | +120% |
| Donchian | +72% | +249% |
| 52-week high | +15% | +55% |
| RSI-2 | +26% | +11% |
| Bollinger | +51% | −47% |

## Does the DEV choice beat random picks out of sample?
| Comparison | TEST | HOLDOUT |
|---|---|---|
| vs 2,000 random 14-pair books | 5th percentile | 67th percentile |
| vs 2,000 shuffles of rules across the same coins | 7th percentile | 60th percentile |
| Quarterly re-pick from the trailing 12 months, vs all pairs | +56% vs +69% | +81% vs +82% |
| Quarterly re-pick vs 200 random re-pick paths | 33rd percentile | 57th percentile |

On DEV, the quarterly re-pick lost 61% against −16% for all pairs.

**Personality persistence** (Spearman across coins, DEV→TEST / DEV→HOLDOUT):
- autocorrelation: +0.21 / +0.16;
- VR(10): +0.07 / −0.20;
- Hurst: −0.13 / −0.06;
- trend-minus-mean-reversion: −0.07 / −0.16;
- coin × rule interaction: +0.02 / −0.02.

None is significant (every p ≥ 0.40).

## Checks
**TEST: 4/8.** Failures:
- **3:** t = 1.13.
- **4:** −2.4% without the best 3 months.
- **6:** beats only 5.1% of random books.
- **7:** one position is 19.9% of profit.

**HOLDOUT: 5/8.** Failures:
- **3:** t = 1.28.
- **6:** beats 66.8% of random books.
- **7:** −10.6% with the top 5 coins removed.

## Portfolio (monthly)
The best DEV mix gives X a weight of 0.

| Window | Mix | Median | Geo | Months up | Monthly DD |
|---|---|---|---|---|---|
| TEST | W1+W2 50/50 | +3.54 | +4.48 | 20/24 | 5.9% |
| TEST | W1+W2+X thirds | +2.75 | +3.33 | 20/24 | 5.2% |
| HOLDOUT | W1+W2 50/50 | +2.85 | +3.30 | 26/36 | 11.5% |
| HOLDOUT | W1+W2+X thirds | +2.81 | +2.77 | 25/36 | 11.4% |

## Verdict: FAIL
- **Per-coin choice:** picking each coin's best rule does not beat random picks out of sample (on TEST it does worse
  than 95% of random books).
- **Coin "personality":** whether a coin trends or mean-reverts does not persist from one period to the next.
- **What does persist:** the rule family. Trend and breakout rules (EMA, Donchian) made money on most coins in all
  three windows.
- **How to run a rule:** on a broad basket of liquid coins, not on hand-picked coins.
