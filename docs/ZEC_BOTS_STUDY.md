# ZEC bots: six TradingView strategies as live-ready bots (2026-10-18)

**Asked for by the operator:** "build bots for them not trading desks". The input was another study's table: the best
single-coin result of six TradingView strategies, all on ZEC, long-only spot, 2024-10-11 → 2026-10-11, 0.10% fee and
0.02% slippage. The sources are fmzquant/strategies at commit 7853bb2b.

## 1. The ports reproduce the table exactly

Each strategy is ported line by line in `app/zecbots/rules.py`, keeping Pine's semantics (SMA-seeded EMA and RMA,
Wilder RSI, CCI with mean deviation, na comparisons false). It is replayed through the research package's cash broker
(`app/tvdesks/broker.replay`) on Binance spot ZECUSDT.

| Strategy (source author) | Candles | Our port | Table |
|---|---|---|---|
| EMA & RSI (fitradn) | 4h | +4,381.41%, DD 45.05%, 47 trades | same |
| Momentum Explosion 2CCI RSI (capam) | 4h | +4,079.93%, DD 55.80%, 12 trades | same |
| EMA Crossover 50/144/200 (SDTA) | 1h | +3,023.56%, DD 52.00%, 156 trades | same |
| MA Crossover SMA20/SMA200 (ccrockatt21700) | 1h | +2,951.76%, DD 57.17%, 70 trades | same |
| Scalp Slayer (Creativ3mindz) | 4h | +2,729.02%, DD 68.07%, 8 trades | same |
| Wilder Volatility Trailing Stop (SparkyFlary) | 4h | +2,092.90%, DD 65.56%, 35 trades | same |

**Source notes:**
- Scalp Slayer's take-profit and stop-loss lines only draw labels. Its orders are the two `strategy.entry` calls.
- Wilder's TS is declared without `var`, so it resets to 0 each candle before the assignment. That is ported as written.

## 2. As the bots will trade: ZECUSDT perpetual, 1x, 30% disaster stop, funding

**Settings:**
- Script: `scripts/zec_bots_study.py`.
- Simulator: `app/macdtrio/sim.py`, now with a `tf` option for 1h candles.
- Fills at the next open.
- Taker costs: 0.05% plus 0.02% per side.

**Results:**

| Bot | Long-only: 2y / DD | Year 1 | Year 2 | Worst month | Long + short: 2y / DD |
|---|---|---|---|---|---|
| EMA & RSI | +5,197% / 44.8% | +719% | +455% | −14.2% | +3,713% / 62.6% |
| Momentum 2CCI RSI | +5,540% / 55.4% | +391% | +868% | −28.3% | +3,651% / 65.2% |
| EMA Crossover (1h) | +4,040% / 48.0% | +449% | +643% | −21.6% | +2,110% / 55.0% |
| MA Crossover (1h) | +3,513% / 55.3% | +311% | +755% | −21.1% | +1,396% / 72.5% |
| Scalp Slayer | +2,073% / 70.5% | +360% | +286% | −61.7% | +883% / 79.5% |
| Wilder Trailing Stop | +1,669% / 63.7% | +302% | +99% | −24.8% | +190% / 75.7% |
| **All six, equal money** | **+3,672% / 40.1%** | | | −26.6% | |
| ZEC buy & hold (no costs) | +3,516% / 72.3% | | | | |

Year 1 and Year 2 above are separate runs that each start flat. The dashboard's year 2 continues the 2-year run.

**Decision: long-only.** The sources also go short. On ZEC, adding the short side lowered the return and raised the
drawdown for **all six**, because the short trades lost money on a coin that rose 35-fold.

**Maker orders** (0.02% plus 0.01%) add between about 15 and 550 points over 2 years. The most is on the 1h EMA
Crossover, with 155 trades.

## 3. What this does and doesn't show (read before going live)

- **Hindsight.** ZEC was the best coin out of a multi-coin study. The table shows each strategy's best coin, so it was
  selected after the fact. Nothing here shows these rules work on a coin chosen beforehand.
- **ZEC's rally carries the results.** ZEC itself rose about 3,500% in the window. Long-only trend rules mostly rode
  it, with a smaller drawdown than holding (40–70% instead of 72%). If ZEC trends down or sideways, expect losses.
  October 2026 to date shows this: EMA & RSI −16%, MA Crossover −17%, Wilder −12%, EMA Crossover −10%.
- **One coin, seven bots.** All six bots and the MACD ZEC bot hold the same coin. They win and lose together, so
  running several multiplies the ZEC exposure. It doesn't diversify it.
- **Scalp Slayer** is 9 trades in 2 years, with a −62% month. **Wilder**'s year 2 was weak (+99%, DD 50%). These two
  are the riskiest.

## Live bots

- **Code:**
  - `app/zecbots/live.py`: `ZecBotLive`, one per strategy, on the shared desk executor `app/live/desk_live.py`.
  - Executor settings: 1x isolated, maker-first orders, a 30% exchange-side disaster stop, the operator's loss limit,
    reconciliation, the mainnet gate and the kill switch.
- **Behaviour:** long-only and full size. Each bot needs its own linked Binance account (one strategy per account).
  It is OFF until the operator switches it on and types `GO LIVE ZEC <KEY>`. It starts flat and acts only on candles
  that close after switching on.
- **Switches:** on the private dashboard's home page ("Your ZEC bots", **Live switch** on each card). The private API
  is `/api/zec-<key>/live`.
- **Public backtests:** `GET /api/public/zec-bots` (from `app/zecbots/backtest.json`, with monthly and weekly returns).
- **Keys:** `emarsi`, `momentum`, `emacross`, `macross`, `scalpslayer`, `wilder`.
