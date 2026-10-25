# PREREG: r4_04_premium (written 2026-10-23, before any TEST run)

Family: perp premium / basis as a directional signal (UM `premiumIndexKlines` 1h from data.binance.vision).
The premium index is the perp's deviation from the spot index, so it is the perp-vs-spot basis; no separate spot download.

## Data and universe
- Premium: `premiumIndexKlines` 1h for every USDT perp that was ever in the point-in-time top-40 (delisted included,
  431 coins, 5,639 archive files, 2 missing). Cache `data/monthly20_r4_premium/`.
- Prices/funding: agent 07's data.binance.vision 1h cache (delisted included). Daily bars = 00:00-24:00 UTC.
- Universe: top-40 by trailing 30-day quote volume known at each day's open, >=30 days listed (r2_rotation rule).

## DEV screen (2021-01..2024-10 only), all reported in dev_grid.json / ic1h.json
- 1h horizon (ic1h.py): the cross-sectional fade has a real gross edge (+3.3 bp/hour) but turnover costs 9-13 bp/hour
  -> dead after costs. Not taken to TEST.
- 4h panel, 144 configs (LEVEL/Z/MOM x fade/follow, TS thresholds): 11/144 net positive on DEV, none with t > 0.9,
  almost all negative at doubled costs -> dead. Not taken to TEST.
- 1d panel, 192 configs: 62 net positive; essentially every cross-sectional FADE config positive, every FOLLOW negative.
  Time-series threshold fades and premium momentum weak.
- Selection rule (dev_select.py): the 8 best 1d configs by DEV t-stat that are positive at doubled cost; run each one's
  DEV neighbour grid; pick the highest DEV t among those with >= 80% of DEV neighbours positive.

## The ONE selected config
`tf24_kindxs_famZ_w7_L60_k5_rb3_modefade`
- Score per coin at the close of day d-1: z = (mean hourly premium over the last 7 days - mean over the last 60 days)
  / std of daily mean premium over the last 60 days.
- Every 3 days at the daily open: long the 5 universe coins with the LOWEST z (deep discount vs own norm), short the 5
  with the HIGHEST z (overheated). Each side 50% of equity split equally (gross 1x), held 3 days, then re-ranked.
- Fill at the day's open, fee 0.05% + slip 0.02% per side, funding every stamp, size <= 1% of prior day quote volume,
  liquidation 1/lev - 0.5% on highs/lows. Judged at 1x; leverage table 1..10x afterwards (never above 10x).
- DEV result (1x): mean month +4.63%, median +2.32%, geo +3.98%, max DD 40.6%, t 2.55, net +503% (doubled cost +355%).
  Without funding +219%; long leg alone +555%, short leg alone -53% (it is a spread, the short leg hedges).

## Neighbour grid for check 5 (17 configs, all else equal)
w (days) 4, 5, 9, 10; L (days) 30, 45, 75, 90; k 2, 4, 6, 8; rebalance every 1, 2, 5, 7, 10 days. (prereg.json)

## TEST
TEST 2024-11-01..2026-10-21 is run once by run.py, which also runs the 8 ROBUSTNESS checks (random-pick baseline: 200
versions with the same per-bar weights on random eligible coins), the leverage table and 2026 YTD. No re-tuning after.
