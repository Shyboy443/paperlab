# PREREG: hks_periodicity (Worker 3 round 3), written before the TEST run

## Idea and sources
Heston, Korajczyk & Sadka (2010, J. Finance) find that a stock's return in a given half-hour predicts its return in the
SAME half-hour on later days. The effect lasts about 40 trading days and is not explained by volume, spreads or size.
- Paper: https://www.bauer.uh.edu/departments/finance/documents/Heston-Korajczyk-Sadka-jf-2010-01-07.pdf (arXiv 1005.3535)
- Crypto, weekday version: Long, Zaremba et al. (2020, FRL), "Seasonality in the Cross-Section of Cryptocurrency
  Returns" https://www.sciencedirect.com/science/article/pii/S154461232030235X . Coins with high past same-weekday
  returns outperform (2016-19).
- Replication doubts: "Revisiting seasonality in cryptocurrencies" (FRL 2024)
  https://ideas.repec.org/a/eee/finlet/v64y2024ics1544612324004598.html finds no robust calendar abnormalities.
  An SSRN hourly study (via Quantpedia) finds the effects sit in a few intraday intervals and are short-lived.
- Related: "The Quarter-Hour Effect" (arXiv 2607.09426) reports periodic algorithmic-trading predictability in
  Binance perps. "Periodicity in Cryptocurrency Volatility and Liquidity" (arXiv 2109.12142).
- I found no published crypto test of the HKS same-hour cross-sectional design itself.

## Rule (lib.py)
- **Blocks.** The UTC day is cut into NB = 24/B blocks of B hours.
- **Signal.** For coin j in block b of day d, the signal is the mean, over days d-K..d-1, of the coin's
  cross-sectionally demeaned return in the same block b. A coin needs at least 80% of those K days.
- **Universe.** The point-in-time top-40 (U40), including delisted coins.
- **Book.** Long the top-k and short the bottom-k, 0.5/k each (1x gross). Entry is at the block open, the book is
  rebalanced every block, and the exit is at the next block open.
- **Hysteresis (buf).** A held coin is kept while its rank stays inside the top/bottom round(buf*k).
- **Costs.** 0.05% fee + 0.02% slippage per side on turnover, plus funding.

## DEV-only exploration (2021-01..2024-10): everything I looked at
- **dev_grid.json** (84 configs, every block traded): B in {1,2,3,4,6,8,12}, K in {5,10,20,40}, k in {3,5,10}.
  Gross (zero cost) is positive almost everywhere, up to +10.4%/month geometric (B4 K40 k3). At baseline costs all 84
  are negative (-0.7% to -58%/month). Turnover is the problem: the signal flips from block to block.
- **control_B4K40k3.log.** Taking the signal from a different block of the same past days gives -6.5% to +5.9% gross,
  against +10.4% for the same block. So the same-block periodicity is real in gross terms.
- **dev_single.json** (98 configs: one traded block per day; B in {1,2,4,6,8}; K in {20,40}; k = 5): 0/98 net positive.
- **dev_buf.json** (15 configs, hysteresis buf in {1,2,3}): the only DEV config net positive is the one below.
- Total configs looked at on DEV: 197.

## The ONE selected config (best DEV net geometric month)
B = 12 (blocks 00-12 and 12-24 UTC), K = 40, k = 3, buf = 3.0, mode = continuation, top-40, 1x.
DEV baseline: geometric month +0.12%, median 0.00%, 23/46 months up. It cannot clear the bar (b). It is
pre-registered because the protocol asks for one config tested once.

**Disclosure.** While timing the code, I ran this exact config once over the full period 2021-01..2026-10, which
includes TEST, and saw only the full-period geometric month (+2.61%). The config had already been chosen on DEV
before that run, and nothing was changed after it.

## Neighbour grid (check 5)
- K in {20, 30, 50, 60};
- k in {2, 4, 5};
- buf in {2.0, 2.5, 3.5, 4.0};
- B in {6, 8} (other rebalance frequencies).

That is 15 neighbours. A neighbour counts as positive when it is net positive on both DEV and TEST at baseline costs.

## Checks
The 8 checks are run exactly as in research/monthly20/ROBUSTNESS.md.
- Check 6: 200 random-score versions with the same structure.
- Check 7: profit by coin, and the largest single position (one coin, one block).
- TEST is run once by run.py.
