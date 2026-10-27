# PREREG: w3r2 sector_rotation (written 2026-10-26 after the DEV grid, BEFORE any TEST run)

## Idea and sources
Industry momentum: Moskowitz & Grinblatt (1999, J. Finance, "Do Industries Explain Momentum?") find that stock momentum
is largely an industry effect: industries that won over the past months keep winning. MSCI ("Industry Momentum", 2021)
confirms industry momentum in a global universe. For crypto, coin-level momentum is documented in liquid coins
(Liu & Tsyvinski, NBER w24877; Liu, Tsyvinski & Wu, NBER w25882; arXiv 1904.00890 "Momentum and liquidity in
cryptocurrencies"), with crash risk ("Cryptocurrency momentum has (not) its moments", FMPM 2025). I found NO academic
test of crypto sector/narrative momentum. The practitioner evidence is narrative scoreboards: CoinGecko's
"most profitable narratives" (2024: AI +2,940%, memes +2,185%, L2 −20.7%; 2025: RWA +186%, L1 +80%, AI −50%, gaming
−75%) shows big, persistent within-year sector dispersion, but also that year-scale leaders reverse (AI 2024 → 2025).
So the honest prior is weak: a monthly-horizon sector trend may exist; the 12-month horizon reverses.
Sources: https://www.coingecko.com/research/publications/most-profitable-crypto-narratives ,
https://www.nber.org/system/files/working_papers/w24877/w24877.pdf , https://www.nber.org/system/files/working_papers/w25882/w25882.pdf ,
https://arxiv.org/pdf/1904.00890 , https://www.msci.com/research-and-insights/paper/industry-momentum ,
https://osuva.uwasa.fi/handle/11111/18877 , https://www.sharpe.ai/narratives (commercial sector tracker, idea only).

## Sector map (frozen; sector_map.py, data/w3r2_sector_rotation/sector_map.json sha256 34d8b0b3...3182)
CoinGecko public `/coins/markets?category=...` (no key, fetched 2026-10-26, cached under data/w3r2_sector_rotation/cg/).
Each of the 683 perps in the survivorship-free daily panel gets ONE label, first hit in this order:
meme > l2 > ai > gaming > defi (incl. DEX, lending, LSD-gov) > rwa > infra (oracle, storage, DePIN, infrastructure,
payments, privacy) > l1 (layer-1, smart-contract-platform). Counts: defi 157, ai 86, l2 75, meme 74, l1 70, infra 48,
gaming 45, rwa 13. 568/683 labelled; 112 of the 161 delisted perps are labelled; inside the point-in-time top-120
universe 88–100% (mean 91%) of coins have a label. Unlabelled coins are never traded. PAXG/XAUT excluded.
**Remaining look-ahead bias (stated, not removable with free data):** labels are TODAY's CoinGecko tags. (1) Narrative
labels that were applied later (e.g. NEAR, ICP, TAO, RENDER as "AI", gaming chains) are back-dated to 2021; (2) coins
CoinGecko no longer tracks (FTT, many delisted 2022 coins) are dropped, a survivorship-type gap; (3) rebrands mapped by
a hand alias list (MATIC→POL, FTM→S, AGIX/OCEAN→FET, RNDR→RENDER, EOS→A, MKR→SKY, LUNA→LUNC). Labels are coarse
(8 groups) to limit (1). The sector map was frozen before the DEV grid; one fix was made before the grid ran (exchange
group dropped and L1 moved to last because CoinGecko tags ARB/OP/MATIC as "smart-contract-platform" and UNI/CRV as
"exchange-based"); no label was changed after seeing any backtest.

## Rule (core.py)
Daily panel (data.binance.vision archive incl. delisted, non-crypto dropped), rebalance every `rb` days from the run
start at the daily open, using closes up to the previous day only.
- Universe: point-in-time top-`univ` by 30-day quote volume (engine_r2.universe_mask), labelled coins only.
- Sector score: equal-weight mean of members' `K`-day return; sectors with < 3 members skipped.
- LOG mode: if BTC's previous close <= its `gate_n`-day SMA → flat. Otherwise long all members of the top `S` sectors,
  sectors equal-weighted, coins equal inside a sector, 1x gross (sum of weights = 1).
- Engine: next-open fills, 0.05% fee + 0.02% slippage per side on traded notional, funding paid/received daily,
  position <= 1% of the previous day's quote volume, short liquidation check (not used in LOG).

## DEV grid (2021-01..2024-10 only; 96 configs = K {14,30,60,90} x S {1,2} x mode {LS, LO, LOG} x univ {40,80,120}, rb 7)
Full table: dev_grid.json / dev_grid.txt. Selection rule (same as Worker 1's): highest DEV t-stat of the monthly mean.
Top of the DEV table (median / geo / mean month %, months up, max DD %, t, net % at 1x, net % at 2x costs):
| K | S | mode | univ | median | geo | mean | up | DD | t | net | net 2x |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **30** | **2** | **LOG** | **120** | **0.00** | **6.44** | **9.56** | **22/47** | **61.3** | **2.28** | **1782** | **1651** |
| 30 | 2 | LS | 120 | −0.47 | 2.19 | 2.51 | 23/47 | 26.5 | 2.04 | 177 | 147 |
| 14 | 1 | LS | 40 | 0.96 | 3.16 | 3.95 | 30/47 | 44.1 | 1.96 | 331 | 265 |
| 30 | 1 | LS | 40 | −0.03 | 3.28 | 4.29 | 23/47 | 39.2 | 1.87 | 356 | 298 |
| 30 | 2 | LOG | 80 | 0.00 | 3.97 | 6.49 | 22/47 | 67.2 | 1.82 | 525 | 479 |
Unfavourable DEV facts recorded now: 23/24 LOG configs were net positive on DEV, but only ~half of LS configs; LO
(ungated) has 85–96% drawdowns; the selected config has a 61% DEV drawdown and a median month of 0 (the gate is flat
in many months), so bar (c) DD <= 23% and the median-month bar are already unlikely to be met.
DEV-only diagnostics: 40 random-pick versions on DEV: strategy +1782% vs random median +410% (beats 40/40);
daily correlation with the Worker 1 book on DEV = 0.14.

## SELECTED (frozen): K=30, S=2, mode=LOG (BTC > 50-day SMA gate), univ=120, rb=7 days, gate_n=50, 1x.

## Checks (ROBUSTNESS.md) exactly as round 1's run.py
1 DEV/TEST separately, baseline and 2x costs; 2 calendar years; 3 t >= 2 and 3-month block bootstrap p5 > 0 (5,000, seed 7);
4 minus 3 best months; 5 neighbours (18, one change each): K {15, 22, 38, 45}, S {1, 3}, univ {60, 90, 150, 180},
rb {3, 5, 10, 14}, gate_n {25, 38, 62, 75}; PASS if >= 70% are net > 0 on DEV and TEST;
6 200 random versions: same gate, same rebalance days, same number of longs each rebalance, coins drawn at random
from the same eligible pool (labelled, in universe, priced), same costs; PASS if the full-period net beats >= 95%;
7 drop the 5 coins with the largest summed position P&L (universe refills) → net > 0, and largest single position
<= 10% of the full-run profit; 8 geo month > 0 and >= 0.5 x mean month.
Also: Worker 1 / Worker 2 monthly and daily correlation, W1+W2+X thirds portfolio, 2026 YTD, Oct 2026, 500 USDT.
TEST (2024-11-01..2026-10-21) is run once by run.py.
