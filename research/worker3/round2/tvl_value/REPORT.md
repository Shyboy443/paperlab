# tvl_value (Worker 3 round 2): FAIL, 6/8 checks, the TVL factor is dead in TEST

The agent could not write this file, so it was saved from its final message.

## Idea and data
Free DefiLlama TVL, fees and revenue used as value/growth factors. The literature prior is weak (Soiman et al. 2023;
Soiman & Mourey).

**Mapping:** 286 coins, delisted included. Classic LUNA could not be mapped.

**No full-history market cap is free**, so the score used is the ratio change instead of the level:
vchg = Δlog TVL − Δlog price.

**Look-ahead risk:** DefiLlama serves today's data vintage, not what was published at the time.

## Selected config (DEV)
vchg L30, top-100 perps with TVL ≥ 20M USD, long top 5 / short bottom 5, 1x, 4 staggered tranches every 28 days. The
tranches remove rebalance-phase luck.

## Results at 1x
| | median mo | geo mo | mean mo | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV | +1.43 | +4.07 | +4.50 | 36/47 | 28.7% | 3.07 | 34.5 |
| TEST | −0.79 | +0.28 | +0.52 | 10/24 | 32.3% | 0.36 | 39.3 |
| Full | +1.15 | +2.75 | +3.13 | 47/70 | 42.7% | 2.81 | 36.2 |

- 2025–26 at doubled costs is −8.8%.

## Checks
6/8 pass. These fail:
- check 5: only 35% of neighbours positive on both halves;
- check 7: the BTW position is 11.6% of profit.

## Portfolio
Adding it to W1+W2 drops the mix to 15/24 months up.

## Verdict
**FAIL** on (a), (b), (c) and (d). The TVL-vs-price signal worked in 2021–24 (mostly the 2021 DeFi boom) and decayed to
about zero in 2025–26.
