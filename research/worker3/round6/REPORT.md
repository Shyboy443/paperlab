# Worker 3, round 6: improving the built rule. Result: 0/3, keep the current rule (2026-10-27)

Every variant below was chosen on DEV only and pre-registered. Each was run once on TEST and once on HOLDOUT. The bar
is in BRIEF.md.

| Agent | Selected variant | TEST geo / DD / up | HOLDOUT geo / DD / up | Verdict |
|---|---|---|---|---|
| current rule | high52 top-20 5/5 daily | +10.33 / 39.9% / 16 | +5.18 / 28.5% / 24 | - |
| signal | short only if 7-day return < 0 | +9.96 / 38.8% / 16 | +4.64 / 34.9% / 21 | FAIL |
| execution | 50% no-trade band | +9.28 / 39.1% / 14 | +5.22 / 24.6% / 24 | FAIL (TEST geo 0.02 short of the floor) |
| risk_overlay | 60-day vol target (down only) + 15% short stop | +8.02 / 25.9% / 15 | +4.43 / 22.8% / 22 | FAIL (not BETTER-RISK: HOLDOUT DD only −5.7 points) |

## Learned
- **Signal:** the edge is the raw 30-day channel position. Making it relative to BTC, adding anti-lottery filters or a
  listing-age filter all lower the return. The meme-perp concentration (check 7) cannot be filtered out without losing
  the edge.
- **Risk overlays:** they trade return for drawdown about one-for-one. Vol targeting is what cuts the drawdown. The
  short stop removes the liquidations but costs return on TEST.
- **Execution:** a no-trade band cuts orders by about 60% for about the same return. The 25% band looked better on both
  windows, but that is seen after the fact, so it is not selectable.
- **500 USDT:** the current rule with Binance's minimum order sizes still makes TEST +10.27 and HOLDOUT +5.10.

Sub-reports are in signal/, execution/ and risk_overlay/.
