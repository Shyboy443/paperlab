# lit_scout (Worker 3 round 1): FAIL

The agent could not write this file, so it was saved from its final message.

`SHORTLIST.md` ranks 8 candidates found in the literature. Most published effects either live in micro caps or overlap
families we already tried.

## Candidate 2: turnover-volatility factor
Based on Mercik, Zaremba & Demir 2026, "Crypto Factor Zoo".

The DEV run failed (17 of 18 configs lost money; the best had t 0.82), so nothing was selected and TEST was not run.

## Candidate 1: stablecoin float timer
Based on Griffin & Shams 2020 and Ante et al. 2021. Data: DefiLlama, used with a 2-day lag.

**Rule:** when 30-day float growth is above 1%, hold the 3 most liquid perps at equal weight, 1x. Otherwise stay flat.

| 1x | median mo | geo mo | mean mo | months up | max DD | t |
|---|---|---|---|---|---|---|
| DEV | 0.00% | +2.71% | +4.20% | 18/46 | 55.6% | 1.56 |
| TEST | 0.00% | +0.33% | +1.04% | 9/24 | 50.1% | 0.42 |

- **Checks:** 4/8. Failed: 3 (t 1.59), 5 (69% of neighbours), 6 (beats only 79% of random timings) and 7 (−5.3% without
  its top 5 coins).
- **Costs:** 2025–26 is −11.8% at doubled costs.
- **Portfolio:** adding it to W1+W2 drops the mix from 20/24 to 17/24 months up.
- **Why it failed:** stablecoin float growth seems to move with prices rather than lead them.

## Verdict
**FAIL** on (a), (b), (c) and (d).
