# token_unlocks (Worker 3 round 1): FAIL, 6/8 checks

The agent could not write this file, so it was saved from its final message.

## Idea
Large team and investor vesting cliffs add supply, which may push prices down.

Sources: Keyrock Dec 2024 (16,000+ unlocks; pressure from about 30 days before big unlocks); a Tokenomist 2026 summary;
a Univ. de Vic thesis. No peer-reviewed event study was found.

## Data
DefiLlama dataset files (the API routes are now paywalled): 195 protocols mapped to Binance perps.

Look-ahead risk: only today's version of each schedule is available. To limit this, only insider and investor cliffs
were used, since their dates are fixed at launch.

## Selected config (DEV, 216-config grid)
- Event: an insider or investor cliff of at least 5% of supply.
- Trade: short at the open 30 days before the unlock, cover at the unlock-day open.
- Hedge: equal-notional BTC long.
- Sizing: min(0.33, 1/n) per event.

## Results
| | median mo | geo mo | mean mo | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV | +1.72% | +4.35% | +4.90% | 28/46 | 20.5% | 2.92 | 1.1 |
| TEST | +5.73% | −0.39% | +1.84% | 15/24 | 69.2% | 0.43 | 4.7 |
| TEST, total exposure capped at 1x | +3.44% | +0.57% | +1.10% | 15/24 | 37.4% | 0.52 | |

- 64% of TEST trades won, but a few short squeezes wiped out the gains: H −61%, RIVER −55%, NOM −19%.
- 2026 YTD: −52.5%.

## Checks
6/8 pass. Check 1 fails (TEST −8.9%), and check 5 fails (only 9 of 16 neighbours positive on both DEV and TEST).

## Correlation and portfolio
- Monthly correlation: −0.26 with Worker 1, −0.05 with Worker 2.
- Adding it to W1+W2 lowers the geometric month (+4.48% → +3.48%) and triples the drawdown.

## Verdict
**FAIL** on (a), (b) and (c); (d) passes narrowly.
