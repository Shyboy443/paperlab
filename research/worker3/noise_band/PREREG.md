# Noise band (Zarattini, Aziz, Barbon 2024, "Beat the Market") on Binance perps: pre-registration

Source: an Instagram reel by quant_labde (2026-10-27). It claims the rule tested well on NASDAQ and S&P futures since
2018. The paper defines the rule for SPY. This test asks whether the same rule works on crypto, as a candidate for
Worker 3.

## Rule
- **Data:** 15m bars for 6 perps (BTC, ETH, SOL, DOGE, XRP, AVAX) from the shared cache.
- **Session:** 13:30-20:00 UTC every day, the US cash session. The fixed UTC hours ignore daylight-saving time. The
  14:30 start is reported too, as a robustness check.
- **Session open `O`:** the open of the 13:30 bar.
- **Reference close `P`:** the 20:00 price of the previous day.
- **sigma(k):** the mean, over the previous 14 sessions, of |close at k / session open - 1|, for each 30-minute mark k.
- **Bands:**
  - `UB = max(O, P) * (1 + sigma)`;
  - `LB = min(O, P) * (1 - sigma)`.
- **Decisions:** at each 30-minute mark.
  - Go long when the close is above UB, and short when it is below LB.
  - A long exits when the close falls below max(UB, session VWAP). A short exits when the close rises above
    min(LB, VWAP).
  - The rule can re-enter, and it is flat at 20:00.
- **Size:** each coin's sleeve is 1/6 of equity. Exposure within a sleeve is min(1, 2% / 14-day daily volatility),
  so the book is at most 1x.
- **Costs:** 0.07% per side on every change in position. Doubled costs are reported too. No funding is charged,
  because positions are only held within the day.

## Judgement
- **Pass requires all of:** DEV mean month > 0 in both halves, TEST median month > 0, and the 8 luck checks.
- **To replace Worker 3 (high52), it must also beat it on TEST:** median month > +10.2%, and DD < 40%.
- **To be useful as an extra leg:** |corr| with Worker 1 and Worker 2 ≤ 0.3, and TEST geo month > +1%.

The test is touched once, after this file is written.
