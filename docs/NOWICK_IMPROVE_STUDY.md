# No-Wick: can the returns be improved? (pre-registered 2026-10-18)

**Starting point:** the live desk (NW-1) loses on its 90-day backtest.
- **5m:** profit factor 0.50, −0.56R per trade.
- **15m:** profit factor 0.67, −0.30R per trade.
- **Why:** on 5m, one ATR is so small that fees and stop slippage cost about 1R per trade.

The forward paper result after 1.5 days (+3.8%) is noise.

## Fixed before running

- **Data:** Binance USD-M perpetual 1-minute candles for the desk's 10 coins (BTC, ETH, SOL, BNB, XRP, DOGE, ADA,
  AVAX, LINK, SUI), 180 days ending 2026-10-17 00:00 UTC.
  - **DEV:** the first 90 days.
  - **TEST:** the last 90 days, untouched until the one evaluation.
- **Realistic fills, applied to every variant including the baseline:** a resting limit order (the entry at the level,
  and the take-profit) fills only when the price trades **through** it, not when it merely touches. The current
  backtest counts a touch as a fill, which flatters it.
- **Rule:** NW-1 unchanged (levels, 1-minute trend, TP 2 ATR, SL 1 ATR, one trade at a time). Costs are unchanged:
  maker 0.02% on the entry and target, taker 0.05% plus 0.02% slippage on stops.
- **Variants:** 6, all principled, none tuned. Timeframe {5m, 15m, 1h} × cost filter {off, on}.
  - **Cost filter:** skip a trade whose planned risk (1 ATR) is below 0.35% of the price, so costs stay under about a
    quarter of 1R.
- **Selection:** the variant with the best DEV total R among those with at least 30 DEV trades.
- **The selected variant passes only if, on TEST:**
  - total R > 0;
  - profit factor ≥ 1.2;
  - at least 30 trades;
  - profitable on at least 5 of the 10 coins.
- **If it fails:** the live desk stays as it is, and the report says so. All six variants are reported on TEST either
  way.

## Results

Run 2026-10-18 (`python scripts/nowick_improve_study.py`; raw numbers in `docs/nowick_study/study.json`).
Complete data: 263,520 minutes per coin.

| Variant (trade-through fills) | DEV (first 90 days) | TEST (last 90 days) | TEST PF | Coins positive (TEST) |
|---|---|---|---|---|
| 5m (the live desk) | −2,588.0R, 5,204 trades | −3,463.0R, 5,477 trades | 0.46 | 0 of 10 |
| 5m + cost floor | −81.1R, 528 | −113.4R, 665 | 0.79 | 3 of 10 |
| 15m (the live desk) | −93.6R, 289 | −122.2R, 264 | 0.54 | 1 of 10 |
| **15m + cost floor (selected on DEV)** | −46.3R, 145 | −29.3R, 101 | 0.66 | 4 of 10 |
| 1h | −5.2R, 10 | −3.7R, 3 | — | 0 of 10 |
| 1h + cost floor | −3.9R, 9 | −1.1R, 1 | — | 0 of 10 |

**Outcome: FAIL.** The selected variant loses on TEST, and every variant loses in both halves.

**Why, before any cost:** gross R per trade (before fees and slippage) over all 180 days is negative.

| Variant | Gross R per trade | Win rate |
|---|---|---|
| 5m | −0.148 | 32% |
| 5m + floor | −0.028 | 33% |
| 15m | −0.162 | 30% |
| 15m + floor | −0.178 | 29% |

A 1:2 target-to-stop needs a win rate above 33% just to break even before costs. The level retest doesn't reach it.
Cheaper execution, a cost floor or a slower timeframe can't turn a signal that is negative before costs into a profit.

**Decision:** the live desk is unchanged and stays paper only. Its forward result after 1.5 days (+3.8%) is noise
against 10,000+ backtested trades. The research options (`fill_through`, `min_risk_pct`, `tf_ms`) stay in
`app/nowick/model.py`, off by default.
