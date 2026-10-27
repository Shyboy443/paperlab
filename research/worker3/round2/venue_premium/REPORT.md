# venue_premium (Worker 3 round 2): FAIL, 5/8 checks

The agent could not write this file, so it was saved from its final message.

## Idea
Price gaps between venues as demand signals. None of the sources has an out-of-sample trading test, so this was
exploratory:
- **Coinbase premium** (US demand);
- **kimchi premium** (Upbit vs Binance × USD/KRW, contrarian);
- **per-coin Upbit excess premium**.

Sources: Makarov & Schoar 2020; Eom 2021; an SMU 2023 paper; CryptoQuant commentary.

## Data
All free and keyless: Coinbase hourly candles, Upbit daily candles (no delisted markets), Yahoo KRW=X.

## DEV grid (684 variants)
- Kimchi contrarian: weak.
- Alt cross-section: mostly negative.
- Coinbase premium timing: consistent on DEV (30/32 long/flat variants positive).

**Selected:** CBPz3/30. Hold 50/50 BTC+ETH perps when the 3-day mean of the Coinbase premium is above its 30-day mean
(z > 0); otherwise flat.

## Results at 1x
| | median mo | geo mo | mean mo | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV | +3.30% | +4.43% | +5.34% | 30/46 | 36.0% | 2.63 | 3.0 |
| TEST | −2.33% | −0.71% | −0.22% | 9/24 | 53.3% | −0.10 | 2.9 |
| Full | +1.60% | +2.70% | +3.51% | 39/70 | 53.3% | 2.25 | 3.0 |

## Checks
5/8 pass. These fail:
- check 1: TEST −15.7%;
- check 5: 42% of neighbours positive on both halves;
- check 7: one holding spell is 14.2% of profit.

## Portfolio
Adding it to W1+W2 drops the mix to 17/24 months up and a +2.88% geometric month.

## Verdict
**FAIL** on (a), (b), (c) and (d). The edge disappeared in 2025–26.
