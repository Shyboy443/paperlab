# venue_premium: pre-registration (written 2026-10-26, BEFORE any TEST run)

## Idea and sources
Prices of the same coin on different venues drift apart when one investor group buys harder than the others. The gap may
signal demand that keeps coming.
- Makarov & Schoar (2020, *J. Financial Economics* 135(2)): cross-venue gaps are large across countries and widen in
  rallies; Korea has the largest gaps. They link gaps to order-flow pressure.
- Eom (2021, *Finance Research Letters* 38) treats the kimchi premium as a sign of speculative trading. A 2023 FRL
  re-examination finds the link is not stable over time.
- An SMU working paper (2023, ink.library.smu.edu.sg/lkcsb_research/7447) finds that changes in a US-investor demand
  premium (GBTC) are the strongest single predictor of daily BTC returns.
- CryptoQuant's Coinbase Premium Index (Coinbase BTC-USD vs Binance BTCUSDT) is read by practitioners as US demand. The
  claims are anecdotal (CoinDesk 2024-07-01; FalconX commentary). A CryptoSlate blog found the level of the kimchi
  premium has about −0.06 correlation with forward returns.

The literature has no out-of-sample trading test, so this study is exploratory.

## Data (free, no keys; cache data/w3r2_venue_premium/)
- Coinbase Exchange hourly candles for BTC-USD and ETH-USD.
- Upbit KRW daily candles (00:00 UTC days) for the 245 current KRW markets that have a Binance perp. Upbit returns 404
  for delisted markets, so the Upbit side has survivorship bias. This affects only the cross-sectional family.
- Yahoo KRW=X daily closes (FRED timed out). The value used for day d is the last FX date strictly before d.
- Binance: the survivorship-free 1h/1d panel (research/monthly20/r2_rotation).

## Signals (lib.py), all known at 24:00 UTC of day d and traded at the open of day d+1
- CBP[d] = mean over day d's 24 hours of (Coinbase BTC-USD close / Binance BTCUSDT close − 1).
- KP[d] = Upbit KRW-BTC VWAP / (USDKRW_lagged × Binance BTC VWAP) − 1.
- EP[d, coin] = log(Upbit VWAP / Binance VWAP) of the coin minus the same for BTC. No FX is needed.

## DEV grid (2021-01..2024-10 only; dev_grid.json, dev_grid.log)
**Timing family** (BTC or BTC+ETH 50/50, daily decisions):
- CBP>0 with smoothing S ∈ {1, 3, 7, 14}, long/flat (LF) or long/short (LS);
- CBP z-score > 0 with S ∈ {1, 3, 7, 14} and z window L ∈ {30, 90}, LF;
- kimchi contrarian, KP z < thr with S ∈ {1, 7}, L ∈ {90, 180}, thr ∈ {0, 1, 1.5}, LF or LS;
- KP level < x with x ∈ {0, 2, 4, 6}%.

**Cross-section family** (EP on Binance perps, Upbit-listed coins only):
- signal: level or change (S-day mean minus 30-day mean), S ∈ {1, 3, 7, 14};
- sign ±, k ∈ {3, 5}, legs L / S / LS, rebalance 1 / 3 / 7 days, universe top-40 / top-80.

DEV findings:
- Kimchi contrarian is weak: 5/24 LF and 1/24 LS variants are positive.
- The EP cross-section is mostly negative: every S-only variant loses; the best LS variant has DD 31–45%; the sign is
  unstable.
- The **Coinbase premium is consistently positive**: 8/8 CBP>0 LF and 15/16 CBPz LF variants are positive. All 8 CBP>0 LF
  variants and 15 of the 16 CBPz variants beat buy-and-hold (DEV geo month: BTC +0.78%, BTC+ETH +1.32%).
- The signal is not a trend filter: its correlation with a price > SMA20 position is 0.02, and with the 3-day return 0.15.

## The ONE selected config (best DEV, family 30/32 positive)
**CBPz3/30 on BTC+ETH, long/flat, 1x.** Each day at 00:00 UTC:
- s = mean of CBP over the last 3 days (d−2..d);
- z = (s − mean of CBP over the last 30 days) / std of CBP over the last 30 days;
- if z > 0, hold 50% BTCUSDT + 50% ETHUSDT perp long at 1x from the open of day d+1 (rebalanced daily to target); else flat.
- Costs: 0.05% fee + 0.02% slippage per side on traded notional, funding paid; doubled-cost run too.

DEV result: geo +4.43%/month, median +3.30%, mean +5.34%, 30/46 months up, max DD 36.0%, 140 switches.
The DD is already above the 23% bar on DEV, so (c) is expected to fail. It is still the honest pick.

## Checks (ROBUSTNESS.md, adapted for a 2-asset timing rule; fixed now)
- 1–4 and 8: exactly as written, on the 70 months 2021-01..2026-10.
- 5, neighbour grid (12): S ∈ {2, 4, 5}; L ∈ {15, 22, 38, 45}; decision every 2 or 3 days (hold in between);
  z threshold −0.25 / +0.25; assets BTC-only. Need ≥ 70% net positive on both DEV and TEST.
- 6, beats chance: 500 random circular shifts of the position series (same time in market, same number of switches,
  same costs). The full-period result must beat ≥ 95%.
- 7: only 2 coins, so "remove top-5 coins" becomes "BTC-only AND ETH-only versions both net positive". Also no single
  holding spell (one continuous in-market run) may contribute > 10% of total profit (sum of spell log returns).
- Correlation with W1/W2 monthly; W1+W2+X portfolio; 500 USDT check (250 USDT per leg ≥ BTC 100 / ETH 20 USDT min).

TEST (2024-11-01..2026-10-21) is run ONCE by run.py.
