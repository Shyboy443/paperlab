# Worker 3 research round 2 (2026-10-26): no candidate passes; one portfolio lead

The bar is the same as round 1 (BRIEF.md).

| Agent | Idea | Checks | TEST median / geo month | Months up | Max DD | Verdict |
|---|---|---|---|---|---|---|
| high52 | Donchian-30 position L/S (anchoring) | 7/8 (5 positions > 10%) | +3.91 / +5.23 | 15/24 | 36.3% | FAIL; best lead |
| w1_upgrade | W2 inside W1's headroom (+ inverse-variance weights) | 7/8 and 8/8 | +4.38 / +5.09 | 14/24 | 19.0% | FAIL |
| sector_rotation | top-2 sector momentum, BTC gate | 5/8 | 0.00 / −0.71 | 8/24 | 61.2% | FAIL |
| tvl_value | TVL-vs-price change L/S | 6/8 | −0.79 / +0.28 | 10/24 | 32.3% | FAIL |
| venue_premium | Coinbase-premium timer, BTC+ETH | 5/8 | −2.33 / −0.71 | 9/24 | 53.3% | FAIL |

## Portfolio lead: high52 as a third leg (TEST, monthly)
| Mix | median | geo | months up | monthly DD |
|---|---|---|---|---|
| W1 + W2 (50/50) | +3.54% | +4.48% | 20/24 | 5.9% |
| W1 + W2 + high52 (thirds) | +4.84% | +4.90% | 20/24 | 4.3% |

It is not eligible because high52 fails check 7, so it needs a forward paper test.

## Other post-hoc leads (not eligible)
- W1 with crash protection (V2): 18/24 TEST months up.
- Trend only when DVOL is calm (round 1).

## Lesson, confirmed again
Four of five families worked in 2021–24 and faded or flipped in 2025–26:
- sector rotation;
- TVL value;
- the Coinbase premium;
- (in round 1) DVOL and the stablecoin timer.

Every round reuses the same 2024-11..2026-10 TEST window, so the chance that something passes by luck grows with each
round.
