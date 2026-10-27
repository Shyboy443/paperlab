# exchange_events (Worker 3 round 3): FAIL, 5/8 checks

The agent could not write this file, so it was saved from its final message.

## Selected config
Short the Binance perp for 48 h after any of five exchange events, pooled:
- Binance spot delisting announcement;
- Binance Monitoring Tag addition;
- Upbit KRW listing;
- Upbit BTC/USDT listing;
- Upbit warning designation.

Entry is at the first full hour after the announcement, sized at 25% of equity per event, 1x gross cap.

Data: Binance CMS and Upbit notice APIs (keyless, with timestamps). Coinbase has no keyless timestamped source.

## Results at 1x
| | median mo | geo mo | mean mo | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV | 0.00% | +1.92% | +2.03% | 23/47 | 16.2% | 2.87 | 1.8 |
| TEST | +9.43% | +1.55% | +5.19% | 15/24 | 78.2% | 1.07 | 11.7 |
| Full | +1.50% | +1.82% | +3.14% | 38/70 | 78.2% | 1.82 | 5.2 |

**Short squeezes:**
- ALPACA delisting, −235% of its slot (April 2025, −69% that month);
- CHIP, −186%;
- HYPER, −119%;
- DEGO, −117%.

## Checks
5/8 pass. These fail:
- check 3: t 1.82;
- check 5: 53% of neighbours. With a 1–3 h entry delay, TEST turns into a loss.
- check 7: one trade is 23.5% of profit.

## Portfolio
Adding it to W1+W2 in thirds keeps 20/24 months up, but the monthly drawdown rises from 5.9% to 21.9%. So it is not
PORTFOLIO-ONLY.

## Verdict
**FAIL**. The 2021–24 "bad news drifts down" effect turned into short squeezes in 2025–26.

Post-hoc lead (not eligible): Binance delisting and Upbit non-KRW listing shorts with a hard stop about 30% against the
position.
