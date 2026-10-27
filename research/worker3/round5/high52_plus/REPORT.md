# high52_plus (Worker 3 round 5): PORTFOLIO, not STRONG

The agent could not write this file, so it was saved from its final message.

**Files** (`lib5.py` reproduces round-4 ORIG exactly):
- PREREG.md: sources, a 40-config grid, the selection rule, and an addendum written before TEST and HOLDOUT;
- the DEV grid: `dev.py`, `dev.log`, `dev.json`, `devtable.md`;
- the single TEST and HOLDOUT runs: `final.py`, `final.log`, `results.json`.

**Sources:**
- George-Hwang 2004;
- Jia et al., "Psychological Anchoring Effect and Cross Section of Cryptocurrency Returns" (SSRN 5386180);
- Zarattini-Pagani-Barbon, "Catching Crypto Trends";
- Cooper-Gutierrez-Hameed 2004;
- Li-Urquhart, MAX momentum.

## DEV grid (40 configs) and what it said
**36 configs in a full factorial:**
- 6 books:
  - L/S;
  - long-only;
  - BTC above its 100-day average then long-only, else cash;
  - the same gate, else L/S;
  - the same gate on the L/S book, else cash;
  - a 2/3 long, 1/3 short tilt.
- 3 exits: daily rebalance, rank hysteresis, or exit below the channel midline.
- 2 channel settings: 30-day alone, or a 20/30/60/120-day blend.

**4 stage-2 configs:** a top-20 universe with 5 per leg, a top-80 universe, volume confirmation, and a trim cap.

**The round-4 insights were re-verified on DEV:**
- the long leg makes 97% of profit;
- +5.4% a month with BTC above its 100-day average vs +2.3% below;
- positions held over 10 days carry the profit.

None of them turned into a better variant:
- **Long-only:** DEV max drawdown 83–91%.
- **Gated long-only:** max drawdown 52–70%.
- **Gated L/S:** max drawdown 20–32%, but a geo month of only 0.6–2.5%.
- **Hysteresis and channel exits:** worse, and concentration jumps.
- **Blended channels, volume confirmation, top-80:** all worse.

**Selection.** No config met the strict rule; ORIG's largest position was 11.8% of profit, above the 10% limit. The
pre-registered relaxed rule (max DD ≤ 40%, largest position ≤ 15%) was used. It picked **top-20, 5 long / 5 short,
30-day channel, daily rebalance, 1x**.

## Results at 1x
| Window | Median | Geo | Mean | Up | Max DD | t | Trades/mo | Net 1x | Net, 2x cost |
|---|---|---|---|---|---|---|---|---|---|
| DEV | +2.78 | +5.13 | +5.78 | 27/46 | 26.3% | 3.22 | 68.5 | +899% | +521% |
| TEST | **+10.23** | **+10.33** | +11.37 | 16/24 | 39.9% | 3.51 | 63.0 | +958% | +738% |
| HOLDOUT | +3.25 | **+5.18** | +5.63 | 24/36 | 28.5% | 3.44 | 54.6 | +517% | +360% |
| ORIG (top-40, 10/10), TEST / HOLDOUT | +3.95 / 0.00 | +5.23 / +3.50 | | 16 / 16 | 36.3% / 25.4% | | | | |

- Every year 2021–2026 is positive: +316%, +14%, +81%, +71%, +308%, +75%.
- The holdout book starts in June 2018; over its 31 active months the geo month is +6.04%.

## Checks
**TEST (2021–26): 7/8.** Check 7 fails:
- the largest position is 15.3% of profit (RAVE);
- the top-5 coins (BEAT, PIPPIN, ZEC, LAB, AKE) made about 78% of profit;
- without them the full period is still +2362%.

These are 2025–26 new-listing meme perps.

**HOLDOUT: 8/8:**
- +219% without the 3 best months;
- +322% without the top-5 coins;
- largest position 5.4%;
- 16/16 neighbours positive;
- beats 100% of random books.

**Correlation:**

| | DEV | TEST | HOLDOUT |
|---|---|---|---|
| with W1 | 0.29 | 0.15 | 0.62 |
| with W2 | 0.15 | 0.22 | 0.04 |

## Portfolio (monthly)
| Window | Mix | Median | Geo | Up | Monthly DD |
|---|---|---|---|---|---|
| TEST | W1+W2 50/50 | +3.54 | +4.48 | 20/24 | 5.9% |
| TEST | **W1+W2+X thirds** | +6.69 | **+6.64** | 19/24 | 4.4% |
| TEST | DEV mix (W1 10 / W2 50 / X 40) | +6.13 | +6.55 | 20/24 | 5.1% |
| HOLDOUT | W1+W2 50/50 | +2.85 | +3.30 | 26/36 | 11.5% |
| HOLDOUT | **W1+W2+X thirds** | +3.69 | **+4.00** | 25/36 | 8.4% |
| HOLDOUT | DEV mix | +3.97 | +4.31 | 25/36 | 7.9% |

## From 500 USDT
- It works: 10 positions of 50 USDT each.
- The BTC perp needs a 100 USDT minimum, so skip it or round it up.
- Daily resizing creates many trades of a few USDT. A minimum trade size is needed, and it was not tested.

## Verdict: PORTFOLIO
STRONG fails on three counts:
- TEST months up 16/24;
- TEST max DD 39.9%;
- check 7 on TEST.

Caveats:
- the selection needed the relaxed rule;
- TEST profit leans on meme perps;
- on its own it is a high-variance book;
- holdout shorts on spot are hypothetical.
