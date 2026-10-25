# Does round 3's IBS rule work on crypto? (pre-registered 2026-10-21)

**Operator:** "so does this strat works on any coins ?"

**The rule, unchanged** (`app/ytstrats/round3.ibs_qqq`):
- IBS = (close − low) / (high − low).
- IBS < 0.10 → buy at the close.
- Sell at the first close above the previous day's high.
- Long only, no stop.

**Markets:** 20 large Binance spot USDT pairs with daily history since 2018 or their listing:
BTC, ETH, BNB, XRP, ADA, SOL, DOGE, LTC, LINK, DOT, AVAX, TRX, BCH, ATOM, XLM, ETC, NEAR, UNI, FIL, AAVE.
- Daily candles close at 00:00 UTC (crypto has no exchange close).

**Costs:** 0.07% per side (0.05% fee + 0.02% slippage, as in every earlier crypto test).

**Windows:** A = 2018-01-01 → 2022-01-01, B = 2022-01-01 → 2026-10-21.

**Pass rule** (the same as round 3):
1. A: mean > 0 and PF ≥ 1.10.
2. B: mean > 0, PF ≥ 1.15 and ≥ 100 trades.
3. B: the lower end of the bootstrapped 95% interval of the mean is above 0.
4. B: positive on at least half of the coins.

**Reported but not part of the verdict:** a comparison with random entry days (the same holding times on random days
of the same coin), plus per-coin results. Nothing is changed after the results are seen.

## Result (run 2026-10-21): FAIL. The IBS edge does not carry over to crypto.

| Window | Trades | Win rate | Mean per trade | PF |
|---|---|---|---|---|
| A 2018–2021 | 801 | 64% | +1.05% | 1.28 |
| B 2022–2026 | 1,696 | 60% | −0.01% | 1.00 |

- **Window B:** the lower end of the confidence interval is −0.44%, and only 9 of 20 coins are positive.
- **Timing:** the rule did *worse* than random entry days in both windows.
  - 2018–2021: −1.23% per trade, z −3.0. Its A profit was the 2020–21 bull run.
  - 2022–2026: −0.15%, z −0.7.
- **Same window on QQQ:** 88 trades, +0.74% per trade, PF 2.19.
- **Coins positive in B:** XLM, LTC, BNB, AAVE, BCH, ETC, DOGE, XRP, SOL. Losers include FIL (−2.17% per trade), ATOM,
  NEAR, AVAX, DOT, ETH and BTC.
  - Choosing the winning coins after the fact would be hindsight; nothing says they would keep winning.
