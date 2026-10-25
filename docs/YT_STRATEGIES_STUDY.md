# YouTube strategy ranking: which "proven 100 trades" claims hold on crypto (pre-registered 2026-10-20)

**Input:** the operator's PDF "Trading Strategy Rankings" (2026-10-19). It lists about 60 YouTube videos ("… Tested 100
Times", "Proven 100 Trades"), each with a reward:risk, a win rate, a timeframe and a "GOA". GOA is the net R over the 100
trades at 1% risk: for example, 54% wins at 1:2 gives 54 × 2 − 46 = 62, shown as 62%.

**What the PDF proves and what it doesn't:**
- Each line is about 100 trades taken by hand, mostly on forex, without fees, chosen and published by the channel.
- With 100 trades, a 54% win rate has a 95% range of roughly 44–64%.
- The PDF has titles and numbers only. It does not give the rules.

**This study** turns the claims into facts that can be checked on our own market: Binance USD-M perpetuals, with real
costs.

## Fixed before any result was seen

- **Strategies:** 27 lines of the PDF whose rules are mechanical and whose timeframe is 15 minutes or longer. Each is
  written down in `app/ytstrats/rules.py` in its standard published form, both directions:
  - the trend filter is the 200 EMA;
  - the "swing stop" is the extreme of the last 10 candles;
  - the target is the video's R multiple.
- **Left out:**
  - Videos whose entries are discretionary (trend lines, Fibonacci, "MTF indicator", London session breakout, pivots).
  - Videos on 1–5 minute charts. At 0.14% round-trip cost, a 1-minute swing stop of about 0.2% costs about 0.7R per
    trade before anything else.
- **Market:** 20 liquid Binance USD-M perpetuals, fixed by liquidity and listing age, not by results: BTC, ETH, SOL,
  BNB, XRP, DOGE, ADA, AVAX, LINK, LTC, DOT, TRX, BCH, NEAR, ATOM, ETC, FIL, APT, ARB, OP.
- **Assets named in the PDF** (operator, 2026-10-20: "if the report says a specific asset, only research that"):
  - The PDF names no coin or pair. It names "Forex" for 2 of the 27: **CMF + MACD + ATR** and **TTM Squeeze**.
  - Those two are tested on the **7 forex majors only**: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD.
- **Second market** (operator, 2026-10-20: "don't we have access to other markets like gold"):
  - Every strategy that names no asset is ALSO tested, as a separate result, on 14 CFD markets from the operator's
    MetaTrader 5 terminal (Fusion Markets, read-only export `scripts/mt5_export.py`): gold, silver, the 7 forex majors,
    US30, US500, NAS100, UK100, GER40.
  - Costs there: half the mean recorded spread plus 0.002% slippage per side, plus 0.0025% commission (raw-spread
    account, about 2.25 USD per lot per side; none on index CFDs).
  - Overnight swaps are ignored.
  - Each market gets its own verdict under the same rule, so there are 52 verdicts in all (more chances of a lucky
    pass: see Multiple testing).
- **Data:** 15-minute candles, aggregated to 30m, 1h and 4h.
  - Year 1: 2024-10-20 → 2025-10-20.
  - Year 2: 2025-10-20 → 2026-10-20.
  - The 200 EMA warms up from 2024-08-01.
- **Execution** (`app/ytstrats/engine.py`):
  - A signal at a close fills at the next open. One position per coin.
  - A candle touching both the stop and the target is settled with the 15-minute candles inside it: whichever came
    first. If both fall in one 15-minute candle, or the strategy itself runs on 15m, it counts as a loss.
  - A gap through the stop fills at the open.
  - Costs: 0.05% taker plus 0.02% slippage per side, converted to R per trade.
  - Funding is ignored (holds last hours to days).
- **Measured for each strategy:** win rate and R per trade, gross (comparable with the video) and net; profit factor;
  drawdown in R; and "GOA per 100 trades" net.

## Decision rule (fixed now)

A strategy is **PROVEN on crypto** only if, with all costs, all of these hold (all 20 coins together):

1. Year 1: mean net R > 0 and profit factor ≥ 1.10;
2. Year 2: mean net R > 0, profit factor ≥ 1.15 and at least 100 trades;
3. Year 2: the lower end of the bootstrapped 95% interval of the mean net R is above 0;
4. Year 2: net R positive on at least half of the assets that traded.

**Multiple testing:** with 52 verdicts, two or three could pass rule 3 by chance. A strategy that passes is
therefore only a **candidate for a forward paper test**. Only a pass there makes it a live-ready bot (switch OFF until
the operator turns it on).

Nothing in these rules is changed after the results are seen. A strategy that fails is reported as failed.

## Result (run 2026-10-20): 0 of 52 pass

`app/ytstrats/results.json`, served at `GET /api/public/yt-strategies`.

- **No strategy passed on any market.** Of the 52 verdicts, only one was net positive in year 2: Ichimoku on the MT5
  markets, +11.4R per 100 trades, PF 1.18. It lost in year 1 (−1.2R per 100), so it fails rule 1.
- **Before costs, every strategy is close to zero.** Gross year-2 results run from about −4R to +4R per 100 trades,
  apart from a few low-trade outliers.
- **The measured win rates sit where a coin flip puts them for each reward:risk:** about 33% at 2R, 40% at 1.5R, 50%
  at 1R, 20% at 4R, 14% at 6R and 65% at 0.5R.
- **The videos' claimed win rates are a median 10 points higher** than measured, with a range of −4 to +29 points.
- **After costs everything loses:**
  - Crypto (0.14% round trip on tight swing stops) costs about 0.1–0.4R per trade.
  - MT5 markets, with much cheaper spreads, lose less but still lose.
- **The 2 "Forex" strategies, tested on the 7 forex majors only, both fail:**
  - CMF + MACD + ATR: −9.5R per 100.
  - TTM Squeeze: −8.9R per 100.

**Conclusion:** on 2 years of data across 34 markets, the PDF's "proven 100 trades" results do not hold. These
indicator setups show no edge before costs and lose after them. No bot is built from them. The rules aren't changed or
re-tuned after seeing the results (that would be curve-fitting).
