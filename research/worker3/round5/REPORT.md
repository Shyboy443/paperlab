# Worker 3 round 5 (2026-10-27): two PORTFOLIO results, no STRONG

The protocol was stricter this round: choose on DEV only, then pass on BOTH TEST and the 2018–2020 HOLDOUT.

| Agent | Idea | TEST geo / median | TEST up / DD | HOLDOUT geo | Checks TEST / HOLDOUT | Verdict |
|---|---|---|---|---|---|---|
| high52_plus | high52 top-20, 5/5, daily | +10.33 / +10.23 | 16/24 / 39.9% | +5.18 | 7/8 / 8/8 | **PORTFOLIO** (best) |
| merged_signal | W1 + a high52 fourth leg (one book) | +5.72 / +7.13 | 16/24 / 18.5% | +3.59 | 8/8 / 8/8 | **PORTFOLIO** (W1 upgrade, corr 0.9) |
| coin_fit | best rule per coin, chosen on DEV | +0.94 / +0.59 | 14/24 / 14.7% | +1.44 | 4/8 / 5/8 | FAIL: does no better than random coin picks |
| lit_scout2 TSD | Donchian ensemble (Zarattini 2025) | +0.51 / −0.20 | 10/24 / 14.6% | +0.81 | 5/8 / 6/8 | FAIL |
| lit_scout2 CTR | trend factor (Fieberg JFQA 2025) | +0.08 / +3.19 | 13/24 / 64.8% | +1.73 | 2/8 / 6/8 | FAIL |

## Best portfolio: W1+W2+high52-top20 in thirds
| | Geo | Median | Up | Monthly DD |
|---|---|---|---|---|
| TEST, W1+W2 50/50 | +4.48 | +3.54 | 20/24 | 5.9% |
| TEST, thirds | **+6.64** | +6.69 | 19/24 | 4.4% |
| HOLDOUT, W1+W2 50/50 | +3.30 | +2.85 | 26/36 | 11.5% |
| HOLDOUT, thirds | **+4.00** | +3.69 | 25/36 | 8.4% |

## Lessons
- **Coin "personality" does not persist.** Choose rules, not coins, and run them on broad liquid baskets.
- **Concentration helps high52.** A top-20 universe with 5 per leg beat top-40 with 10 per leg on all 3 windows. The
  downside is a 40% drawdown when it runs alone, and 2025–26 profits that lean on meme perps.
- **Changes that hurt high52:** long-only, BTC regime gates, and holding winners longer all made it worse on DEV.

## Next step
A forward paper test of high52-top20 as Worker 3, run as a third leg next to W1 and W2.
