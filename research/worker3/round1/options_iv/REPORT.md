# options_iv (Worker 3 round 1): FAIL, 4/8 checks, and harmful as a portfolio add-on

The agent could not write this file, so it was saved from its final message.

## Idea
Once a day, go long, short or flat on BTCUSDT and ETHUSDT perps based on Deribit DVOL (30-day implied volatility, public
API, history from 2021-03).

- Sizing: 50% of equity per coin, 1x.
- Timing: the signal is taken at 00:00 UTC and the trade fills at the 01:00 UTC open.

Sources:
- arXiv 2410.15195 and arXiv 2009.11007 (crypto's inverse leverage effect);
- Alexander & Imeraj (BTC variance risk premium);
- one blog, treated as anecdotal.

Skew and term-structure history is not available from the public API, so they were not tested.

## DEV grid
177 configs in 4 families: implied-minus-realised volatility, DVOL change, DVOL level, and trend switched by implied vol.

**Selected** (highest DEV t-stat): `ivchg k=7 th=10% contra`. Long if DVOL rose more than 10% in 7 days, short if it fell
more than 10%.

## Results at 1x
| | median mo | geo mo | mean mo | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV | +1.24% | +3.37% | +3.70% | 25/46 | 37.6% | 3.00 | 5.4 |
| **TEST** | **−0.99%** | **−2.25%** | −2.07% | **7/24** | **44.6%** | −1.70 | 3.4 |
| Full | 0.00% | +1.40% | +1.72% | 32/70 | 44.6% | 1.79 | 4.7 |

- 2026 YTD: −39.2%.
- The rank correlation between DEV t-stat and TEST result across all configs was −0.15, so DEV did not predict TEST.

## Checks
4/8 pass. These fail:
- check 1: TEST −42%;
- check 3: t-stat 1.79;
- check 5: 0 of 10 neighbours positive on both DEV and TEST;
- check 7: the biggest single position is 12.6% of profit.

## As a portfolio add-on (TEST)
Adding it to W1+W2 drops the mix from 20/24 to 14/24 months up, and the geometric month from +4.48% to +2.31%.

## Post-hoc (not a candidate)
After the single TEST run, the "trend only when DVOL z < 0.5" variants did well on TEST: the best had geo +6.49% and 17/24
months up. But they were flat on DEV, and plain trend did similar on TEST. That reflects the 2025–26 trend, not this
signal. Testing the idea properly would need a new forward test.

## Verdict
**FAIL** on (a), (b), (c) and (d).
