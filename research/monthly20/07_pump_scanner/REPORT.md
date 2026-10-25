# Agent 07: top-gainers scanner. Verdict: FAIL

**The idea.** We scanned every Binance USDT perp on 1h bars: 524 coins trading today plus every delisted perp the
public data portal still holds, 878 coins in all. A coin counts as pumping when it rises hard (4h or 24h return),
trades 3-5x its normal volume, or makes a new 30-day high. We asked whether it keeps going or fades, and traded
three ideas on it:
- ride the pump with a trailing stop;
- short the exhaustion after a blow-off;
- the mirror image: buy the coins that dumped 20% in 24h.

Only coins trading at least about $5M a day were used. Fills could take at most 1% of the bar's volume, with 5 positions at a time.
Costs: 0.07% per side, plus funding and liquidation. Data comes from data.binance.vision (fetch_vision.py).

**What the DEV data (2021-01..2024-10) says about pumps** (dev_event_study.json):
- After a +30% day, the *median* coin fades: about -3% after 24h and -3% after 48h.
- The *mean* is positive (+1% to +2%), because a few coins keep running 50-500%. The returns are strongly right-skewed.
- So fading pumps looks right most of the time, but the tail squeezes kill it. Plain shorting of +30% coins lost
  about 9%/month in all 4 DEV variants, with a 99% drawdown.
- Riding pumps captures the tail but loses on the median. On DEV it only worked in 2021-22.
- **Survivorship bias is large here.** On today's coin list only, "buy the 24h dump" made +6.1%/month (geometric) on DEV
  (dev_results_today_list.json). With the delisted coins added (LUNA, FTT, OM, ...), it fell to +2.4%/month with a 76% drawdown.

**TEST (2024-11..2026-10), pre-registered, run once** (PREREG.md). Cells show the % return for each month.

| months | A1 MOM 1x | A2 MOM 3x | A3 BOUNCE 1x | A4 SHORT_EXH 1x |
|---|---|---|---|---|
| 2024-11..2025-02 | -2 / -16 / -16 / -21 | -23 / -44 / -41 / -52 | +9 / -8 / -4 / +9 | +21 / -2 / 0 / -20 |
| 2025-03..2025-06 | +28 / +43 / +27 / -3 | +99 / +127 / +78 / -16 | -21 / +137 / +79 / +18 | 0 / -55 / -24 / +29 |
| 2025-07..2025-10 | +54 / -2 / +62 / -2 | +191 / -23 / +149 / -27 | -39 / +74 / -46 / -45 | +12 / -17 / -25 / +11 |
| 2025-11..2026-02 | -25 / -29 / +9 / -17 | -64 / -68 / +9 / -50 | +15 / +76 / +20 / -67 | +41 / -15 / -35 / +14 |
| 2026-03..2026-06 | -37 / -20 / +9 / -33 | -78 / -81 / -5 / -77 | -5 / +58 / +68 / -78 | -17 / +13 / +3 / -9 |
| 2026-07..2026-10 | -34 / -4 / +43 / -3 | -75 / -44 / +143 / -27 | +40 / -33 / +105 / 0 | -8 / -57 / +10 / -9 |

| TEST | mean month | median | geo month | worst month | max DD | net | trades | mean at 2x costs |
|---|---|---|---|---|---|---|---|---|
| A1 LONG_MOM 1x | +0.5% | -3.2% | -3.1% | -36.8% | 93.8% | -53% | 1638 | -1.4% |
| A2 LONG_MOM 3x | +0.0% | -27.3% | -27.2% | -80.6% | 100% | -99.95% (ruined) | 1637 | -9.7% |
| A3 LONG_BOUNCE 1x | **+15.0%** | +8.9% | +0.4% | -77.5% | 92.0% | +11% | 2544 | +11.7% (geo -2.5%, net -45%) |
| A4 SHORT_EXH 1x | -5.9% | -5.3% | -9.2% | -56.9% | 94.0% | -90% | 1438 | -7.4% |

**Leverage.** On DEV, the best rule (A1) reaches a +20% *arithmetic* mean month only at 3x. Even then its compounded
result is -0.2%/month with a 98.7% drawdown. At 5x and 10x it is wiped out on DEV, and A3 is wiped out at 5x. On TEST,
A2 (the 3x version, pre-registered) lost 99.95%.

The +15% mean month of A3 at 1x is a mirage. It comes from ±70-137% swings. The geometric return is +0.4%/month, the
drawdown is 92%, and doubled costs turn the net to -45%. Any leverage on it would have been ruined in Feb-2026 or
Jun-2026 (-67%, -78% at 1x).

**Ruin risk.** With the 15-20% stops these coins need, 3x or more blows up within months, in both DEV and TEST.
Short squeezes are why every short rule failed: after a +50% day, the next leg is often another +50%.

**Capacity** was not the binding problem: only 0-90 entries were cut to 1% of the bar's volume. The losses come from
the signal, not from size.

**Verdict: FAIL.** No pre-registered attempt reaches a +20% TEST mean month. The best compounded result is A3 at
+0.4%/month, and it loses at doubled costs.

**Caveats.**
- The "biggest trade share" fields are null because summed USDT trade P&L is not positive under compounding.
- October 2026 funding files were not yet published.
- Stock-token perps are in the universe but almost never trigger the signals.
