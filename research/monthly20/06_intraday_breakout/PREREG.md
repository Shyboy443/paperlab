# PREREG - agent 06, intraday volatility-expansion breakouts

Written 2026-10-23 after the DEV scan (2021-01..2024-10) and BEFORE any TEST data (2024-11..2026-10) was simulated.
Not to be edited after the TEST run. Exact parameters are in `prereg_params.json` (same content as below).

## DEV outcome that drives this
- 360 variants scanned (ORB at Asia 00:00 / London 07:00 / US 13:30 UTC; BB-inside-Keltner squeeze; prior-day high/low
  break; ATR trail k 1.5/3/5; time exit; with/without a 10-day EMA trend filter).
- Pre-declared selection rule: >= 300 DEV trades and net > 0 bp/trade in BOTH DEV halves (2021-22, 2023-24/10).
  **No variant qualified.** Fallback (declared in run.py): highest DEV net bp/trade among >= 300 trades.
- 150/360 variants had positive gross edge; only 21/360 stayed positive after costs (about what noise gives).
- DEV leverage sweep of the selected variant: no leverage reaches +20%/month; mean month peaks at +0.98% at 2x and
  turns to ruin by 10x. **Leverage needed for 20%/month on DEV: none exists (null).**

## Universe, data, execution (all attempts)
- Fixed 6 Binance USDT perps: BTC, ETH, SOL, DOGE, XRP, AVAX (15m bars; chosen as today's large liquid coins ->
  survivorship/selection bias in their favour).
- Signal on a closed 15m bar, fill at the next bar's open as a TAKER (breakout entries are stop entries; a maker
  entry would need a retest that only fills on losers, so it is not assumed).
- Exit: resting trailing stop at extreme - k x ATR(14, 15m), filled at the stop or the gapped open; time exit at
  bar close of the deadline. One position per coin.
- Costs: 0.05% fee + 0.02% slippage per side; real funding; reruns at 2x costs.
- Sizing: isolated margin = equity/6 per trade, notional = L x margin, capped at 1% of the signal bar's quote volume.
  Liquidation when adverse excursion >= 1/L - 0.5% (lose the margin).

## Attempts (all reported)
1. **A1_us_orb_L1 (primary, decides the verdict)**: US-open range = 13:30-14:30 UTC (R=4 bars); first close beyond it
   within the next 2h (W=8) in the direction of close vs 10-day EMA; trail k=5 ATR; time exit 24h after 13:30
   (H=96). Leverage 1.
2. A2_us_orb_L5: same, leverage 5 (to show what an attempt at the target does).
3. A3_squeeze80_L1: squeeze (BB 80 / KC 1.5 x mean TR 80) on for >= 8 bars, then a close outside the band, with the
   trend filter; trail k=5; time exit 16 bars. Leverage 1. Exploratory: only 72 DEV trades (fails the 300 rule).
4. A4_squeeze80_L5: same, leverage 5.
- Also a descriptive TEST leverage sweep (1..30x) of A1, not a separate attempt.

## PASS rule (protocol)
A1 TEST mean month >= +20% at baseline costs AND mean > 0 at doubled costs AND not wiped out AND no single trade
> 50% of profit. Expected: FAIL.
