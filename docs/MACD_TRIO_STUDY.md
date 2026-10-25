# MACD trio: three more coins for the MACD rule, long and short (pre-registered 2026-10-17)

**The ask:** three live-ready bots like the MACD desk. They use the same rule, unchanged, on three new coins, trading
both directions.

**Why a strict selection is needed:** the MACD ZEC result (+2,985%) came mostly from ZEC's own run (+3,500% buy and
hold). Across 84 coins the same rule made about +11.5%. Picking the coins that did best over the whole history would
repeat that hindsight. So the coins are chosen on the **first year only** and must also pass the **second year**,
which the choice never saw.

## Fixed before any data was loaded

- **Rule:** MACD 12/26/9 on 4h closes, with SMA-seeded EMAs (`app/macd/model.channels`, unchanged).
  - A bullish cross means go long. A bearish cross means go short.
  - An opposite cross reverses the position: close, then open the other side.
  - Always in the market after the first cross.
- **Market:** Binance USD-M perpetuals at 1x, the whole account per position.
- **Fills:** the next 4h open.
- **Costs per side:** commission 0.05% (taker) plus adverse slippage 0.02%.
- **Funding:** at every settlement while in a position, at Binance's recorded rate and settlement mark price. Longs pay
  positive funding; shorts receive it.
- **Disaster stop:** 30% against the entry, checked on the 4h high/low, the same as the live executor
  (`app/live/desk_live.py`). Being stopped out leaves the account flat until the next cross.
- **Universe:**
  - Binance USD-M USDT perpetuals that are trading now and were listed on or before 2024-08-01;
  - the 60 largest by 24h quote volume;
  - no ZEC (already live), no stablecoins, no index contracts.
- **Windows:** each year starts flat with 10,000 USDT. Earlier candles are used only for warm-up.
  - **Y1 (selection):** 2024-10-11 → 2025-10-11.
  - **Y2 (validation):** 2025-10-11 → 2026-10-11.
- **Selection:** rank every coin by its Y1 net return after all costs. The 10 best are the candidates.
- **A candidate passes Y2 only if every one of these holds:**
  - net return > 0 after all costs;
  - profit factor ≥ 1.2;
  - close-equity max drawdown ≤ 50%;
  - at least 8 trades.
- **The bots:** the 3 highest-ranked candidates (by Y1) that pass Y2. If fewer than 3 pass, fewer bots. The bar is not
  lowered.
- **Skill check:** compare the Y2 median return of the 10 candidates with the Y2 median of all 60 coins. If the
  candidates aren't better, the selection showed no skill, and the report says so.

## Results

Run 2026-10-17 (`python scripts/macd_trio_study.py`; raw numbers in `docs/macd_trio/study.json`). 60 coins tested,
none skipped.

**Simulator check:** it reproduces the audited ZEC perp result exactly. For 2026 Jan–Sep, long only, it gives
+505.64% with 54 trades, the same as the operator's table.

**Outcome: no coin passes. No bot is deployed.**

| Candidate (top 10 by Y1) | Y1 | Y2 (unseen) | Y2 PF | Y2 max DD | Y2 buy and hold | |
|---|---|---|---|---|---|---|
| WLD | +1,162.1% | −2.7% | 0.99 | 53.1% | −36.3% | fail |
| DOGE | +449.1% | −64.6% | 0.71 | 72.5% | −55.6% | fail |
| MAGIC | +436.3% | −67.5% | 0.72 | 79.2% | −13.0% | fail |
| AVAX | +232.0% | −72.7% | 0.68 | 83.2% | −49.8% | fail |
| DOT | +227.1% | −46.5% | 0.85 | 73.5% | −57.2% | fail |
| LDO | +209.0% | −63.1% | 0.76 | 76.5% | −47.8% | fail |
| RSR | +196.5% | −79.5% | 0.72 | 85.8% | −61.4% | fail |
| ETH | +162.8% | −44.2% | 0.81 | 62.7% | −34.4% | fail |
| XLM | +148.5% | +24.9% | 1.08 | 48.5% | −38.4% | fail (PF) |
| ICP | +143.8% | +49.4% | 1.06 | 71.9% | +3.5% | fail (PF, DD) |

- **Skill check:** the Y2 median was **−54.8% for the candidates**, against **−40.8% for all 60 coins**. Picking by
  year 1 did worse than picking at random.
- **Whole universe, Y2:** only 14 of 60 coins were profitable at all. Only STX and AAVE would have met the Y2 bar. STX
  ranked 23rd in Y1 (+24.6%) and AAVE 51st (−65.4%), so no Y1 rule would have picked them in advance.
- **Why it loses:** about 170 reversals a year on 4h cost about 0.14% each, roughly 24% a year in fees and slippage
  alone, before any whipsaw.
- **Second test, labelled exploratory:** long only (the live MACD bot's mode), same selection and same bar. Also 0 of
  10 pass. The Y2 median was −33.8% for the candidates against −29.6% for all coins, and 13 of 60 were profitable.

**Conclusion:** the MACD 12/26/9 4h rule has no edge that carries from one year to the next, in either direction,
once its coin is chosen fairly. The ZEC result reflects ZEC's own rise. Re-running variants (other settings, other
timeframes) on the same two years until something passes would be curve fitting, so that wasn't done.

## Afterwards: the operator's live bots (2026-10-17)

The operator asked for live-ready bots anyway.
- **Coins:** from the 5 coins positive in both years (`docs/macd_trio/consistency.json`), they chose **STX, DASH and
  XLM**. These are **hindsight picks**.
- **Drawdown:** after "drawdown is too much", two risk changes were compared over the same 2 years
  (`docs/macd_trio/risk_grid.json`):
  - **tighter stops (15/10/7%):** not consistent across the coins (XLM year 2 fell from +24.9% to −7.4% with a 10%
    stop), so the 30% stop was kept;
  - **position size:** cut the drawdown on every coin and in both years. The operator chose **a third**.

| Bot (a third per position, 30% stop) | 2 years | Year 1 | Year 2 | Max DD | Worst month | Profitable months | Worst week |
|---|---|---|---|---|---|---|---|
| STX | +73.9% | +19.7% | +48.4% | 28.6% | −9.4% | 16 of 25 | −11.8% |
| DASH | +444.6% | +16.4% | +364.4% | 22.7% | −11.4% | 15 of 25 | −7.9% |
| XLM | +82.4% | +62.6% | +15.0% | 21.4% | −11.6% | 13 of 25 | −10.4% |

Live: `app/macdtrio/live.py`, with `SIZE = 1/3` in `app/live/desk_live.py`. The ZEC and Donchian desks stay at full
size.

### Improving returns without more drawdown (2026-10-17)

Two standard overlays were tested across all 60 coins, each year judged separately (`docs/macd_trio/improve.json`):
- **Trend filter (200-candle EMA: longs only above it, shorts only below):** lower drawdown and a better Y2 (median
  −8.3% → +2.8%), but a much worse Y1 (+15.5% → +1.5%), and it cut returns on DASH and XLM. **Rejected:** inconsistent
  between the years.
- **Volatility sizing (target set on Y1 only):** no real change (Y2 median −7.5% against −8.3%). **Rejected.**
- **Diversification (accepted):** the three bots rarely lose at the same time. Run together at **half size each**, the
  combined 2-year result is **+393.5%** with a **23.2%** max drawdown. That compares with +200% and 15.6% at a third.
  Each bot alone swings 31–41%. Without DASH (STX + XLM only) it's +116% with a 25.7% drawdown, so DASH's
  single big run supplies most of the return.

| At half size | 2 years | Max DD | Worst month | Profitable months | Worst week |
|---|---|---|---|---|---|
| STX | +110.0% | 40.6% | −13.9% | 16 of 25 | −17.4% |
| DASH | +948.4% | 32.5% | −16.8% | 15 of 25 | −11.7% |
| XLM | +122.2% | 30.7% | −17.1% | 13 of 25 | −15.3% |
| **All three together** | **+393.5%** | **23.2%** | **−7.9%** | 15 of 25 | −8.7% |

**Five bots considered, not adopted (2026-10-17):** adding SAND and ICP (the other two coins positive in both years)
needs 0.55 size to reach the same ~23% combined drawdown. That gives +331% over 2 years (Y1 +62%, Y2 +217%, worst
month −6.5%), against +394% (Y1 +46%, Y2 +284%) for the three at half size. It is more balanced and relies less on
DASH, but the total return is lower. The operator kept **3 bots at half size**.

### Cheaper execution: maker orders (2026-10-17/18)

The signal can't be improved honestly any further on the same two years, but costs can. About 170 reversals a year
each pay the taker fee plus slippage. Three bots together, at half size:

| Execution | 2 years | Max DD | Y1 | Y2 |
|---|---|---|---|---|
| Market orders: taker 0.05% + slippage 0.02% (the card numbers) | +393.5% | 23.2% | +46.1% | +283.6% |
| Fees paid in BNB (−10%), a Binance account setting | +402.1% | 23.1% | +47.4% | +286.8% |
| **Maker: post-only limit 0.02% + 0.01% adverse** | **+466.1%** | **22.2%** | +57.0% | +309.9% |
| Maker, no slippage (ideal) | +485.8% | 22.0% | +59.8% | +316.7% |

**Built:** `DeskLive.MAKER` (on for the three coin bots only; ZEC and Donchian stay on market orders).
- **Entry:** a post-only (GTX) limit at the best bid (buy) or ask (sell), re-priced every 30 s. After 3 minutes, the
  rest goes as one market order.
- **Rejected orders:** if Binance refuses one because it would cross (EXPIRED or −5022), it is placed again.
- **Exit:** the 30% disaster stop stays on the exchange until the position is flat.
- **Switch-off and kill switch:** the waiting stops at once. An entry keeps what filled; an exit sends the rest at
  market.

**Realistic gain:** between the two rows. Limit orders fill more often when the price moves against you, and the
fallback pays the taker fee. The live log records `how` (maker, maker+market or market) for every fill, so the real
share can be checked. Tests: `tests/test_maker_execution.py`.

### Full size, 1x (2026-10-18)

The operator: "drawdown is fine if the results are better, without liquidating". Sizes from half to 2x leverage were
compared, with three bots together, maker costs, and the 30% stop.
- **The stop held every time:** no trade moved more than 30% against its entry, and no candle gapped past it.
- **Liquidation distance:** about 49% at 2x and about 32% at 3x. 3x and above were ruled out, because the 30% stop
  sits next to the liquidation price there.

| Size | Together, 2 years | Max DD | Worst month | Y1 | Y2 | Each bot's DD |
|---|---|---|---|---|---|---|
| half | +466% | 22% | −7% | +57% | +310% | 28–39% |
| 0.75 | +1,066% | 35% | −15% | +78% | +711% | 39–54% |
| **1x (chosen)** | **+2,023%** | **46%** | **−24%** | **+89%** | +1,391% | 49–66% |
| 1.5x | +4,660% | 64% | −40% | +80% | +3,751% | 69–83% |
| 2x | +6,228% | 76% | −53% | +40% | +6,785% | 85–92% |

**Beyond 1x, Y1 gets worse** (+80%, then +40%): leverage only pays because of DASH's year-2 run. **1x full size was
chosen** (`SIZE = 1.0`; no leverage, so liquidation is impossible).

The cards and `app/macdtrio/backtest.json` show the conservative market-order costs: +1,516% together, 48.1% max DD,
worst month −25.2%. The choppy-filter paper test (docs/MACD_CHOP_TEST.md) keeps the half size it was registered
with. It compares FILTER against CONTROL at the same size, so the comparison still holds.
