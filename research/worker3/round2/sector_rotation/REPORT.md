# sector_rotation (Worker 3 round 2): FAIL, 5/8 checks, TEST lost money

The agent could not write this file, so it was saved from its final message.

## Idea
Industry momentum (Moskowitz & Grinblatt 1999; MSCI 2021) applied to crypto narratives. No academic paper tests crypto
sector momentum directly.

## Sector map
Built from CoinGecko categories (no key) into 8 groups; 568 of 683 perps are labelled.

Remaining bias:
- today's labels are used back to 2021;
- coins CoinGecko has dropped can never be picked.

## Selected config (DEV, 96 configs)
- Each week, rank sectors by the equal-weight 30-day return of their members in the top-120 universe.
- Go long the members of the top 2 sectors.
- Stay flat when BTC is at or below its 50-day SMA.

## Results at 1x
| | median mo | geo mo | mean mo | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV | 0.00 | +6.44 | +9.56 | 22/47 | 61.3% | 2.28 | 32 |
| TEST | 0.00 | −0.71 | +0.67 | 8/24 | 61.2% | 0.19 | 38 |
| Full | 0.00 | +4.03 | +6.60 | 30/70 | 72.0% | 2.15 | 34 |

- TEST net is −15.7%.
- 2025–26 at doubled costs is −41.4%.

## Checks
5/8 pass. These fail:
- check 1: TEST negative;
- check 5: only 2 of 18 neighbours positive on both DEV and TEST;
- check 7: MYX alone is 30.6% of profit.

## Portfolio
Adding it to W1+W2 lowers the mix to 15/24 months up and a +3.01% geometric month.

## Verdict
**FAIL** on (a), (b), (c) and (d). The DEV edge came from the 2021 and early-2024 alt rallies; in TEST the leading sector
kept changing.
