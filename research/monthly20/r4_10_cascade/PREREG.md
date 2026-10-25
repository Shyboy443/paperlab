# r4_10_cascade - PRE-REGISTRATION (written 2026-10-23, before any TEST bar was simulated)

Machine-readable copy: `prereg.json` (run.py reads it; neither file is edited after the TEST run).

## Idea
Liquidation cascades: a big fast move + a volume spike + a sharp open-interest DROP (positions force-closed). When
the forced flow is done, price partially reverts. Trade against the move. (The mirror idea, "big move WITH rising OI
continues", was tested on DEV: 0 of 32 configs positive, raw event study also negative -> dropped, reported.)

## Data / universe
- Binance USD-M perps from data.binance.vision (keyless): 5m klines, 5m `metrics` open interest, funding (agent 04 cache).
- Universe point-in-time: each calendar quarter, top 20 USDT perps by trailing 90-day quote volume (agent 04's
  survivorship-free 1h archive, delisted coins included: LUNA, FTT, MATIC, FTM, TOMO, WAVES, BLZ, LOOM, LINA ...).
  TradFi perps excluded. 101 coins over 2021-Q4..2026-Q4.
- **OI coverage:** the archive's metrics start 2021-12-01 for almost every coin (BTC 2020-09). So 2021-01..2021-11
  is almost entirely flat (0% months). This is a data limit, not a choice.

## Rule (selected config)
At the close of 5m bar k, window W = 24 bars (2 hours):
- z = log(c[k]/c[k-24]) / (sigma_5m x sqrt(24)), sigma = std of 5m log returns over the 3 days before the window: |z| >= **7.0**
- quote volume of the 24 bars >= **5.0** x the 3-day average (per bar)
- OI at close of k / OI at close of k-24 - 1 <= **-4%**
- Direction: against the move (long after a dump, short after a pump). confirm = 0 (no wait).
- Entry at the OPEN of bar k+2 (one extra 5m bar after the OI stamp). Stop = **2.0 x ATR1h** (mean 12-bar range of
  the prior day); time stop **144 bars = 12 h**; no target. Stop checked first in a bar; gaps fill at the open.
- Portfolio: **K = 5 slots**, one per coin, notional = lev x realised equity / 5, capped at 1% of the entry bar's quote volume.
- Costs: 0.05% fee + 0.02% slippage per side, funding per 8h stamp; doubled-cost run. Liquidation at 1/lev - 0.5%.

## DEV parameter grid (464 configs, dev_grid.json)
revert: W {6,12,24} x Z {3.5,5,7} x V {3,5} x D {2%,4%} x confirm {0,1} x H {36,72,144} x S {2,4} (432);
cont: W {6,12} x Z {3.5,5} x V {3} x D {2%,4%} x H {36,144} x S {2,4} (32). K = 5 everywhere.
DEV results: revert 254/432 net positive, 168/432 positive at doubled costs; cont 0/32 positive.

**Selection rule (fixed in dev_grid.py before running):** among configs with >= 150 DEV trades and DEV net > 0 at
doubled costs, the highest t-stat of the DEV monthly mean. Winner = the config above:
DEV mean month +1.20%, median 0.0%, geo +1.14%, max DD 7.3%, net +68.2%, 252 trades, t = 2.25, 2x-cost net +56.9%.
Note: it sits on the edge of the grid (largest W, Z, D); the +25/+50% neighbours below test beyond it.

## Neighbour grid for check 5 (27 configs, one parameter changed at a time)
Z {3.5, 5.25, 8.75, 10.5}; V {2.5, 3.75, 6.25, 7.5}; D {2%, 3%, 5%, 6%}; H {72, 108, 180, 216};
S {1.0, 1.5, 2.5, 3.0}; W {12, 18, 30, 36} (the "frequency" variants); confirm = 1; K = 3; K = 8.

## Checks / leverage
All 8 checks of ROBUSTNESS.md at 1x over 2021-01..2026-10 (check 6: 200 random versions with the same entry times,
directions, exit rules and sizing, coin drawn at random from that quarter's universe). Then leverage 1..10x
(never above): the smallest leverage giving a TEST arithmetic mean month >= 20%, or "not reachable".
TEST (2024-11-01..2026-10-21) is run once. Any change after that is a new, separately reported attempt.
