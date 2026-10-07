# V14 HTF: the best strategies, higher timeframes first (paper)

The operator asked on 2026-10-04:

> "create another set of bots just same strategy of most winning and returning bots ... the new bots should have a new
> rule it should analyze higher time frames as well before taking trades, that's what real humans do"

## Which strategies are copied

These were the leaders by return on 2026-10-04 10:55 UTC.

| V14 bot | Copies | Leader it came from | Coins |
|---|---|---|---|
| V14.1 (×6) | V8.3 VWAP snap-back | Jinx (ARB) +2.3%, Jinx Ladder +3.6% | V8's 6 coins: ETH SOL XRP DOGE ARB ENA |
| V14.2 | V11.1 relative-strength breakout scanner | Juno +1.2% | 30 |
| V14.3 | V11.2 relative-strength pullback scanner | Kilo AI +1.2% | 30 |
| V14.4 | V13.1 Snapback (limit orders) | Bounce +1.2% | 29 |

Sable (V7.1, +4.6%) is **not** copied:
- it needs V7's own Bybit funding / open-interest feed;
- it already trades only with the 1h trend and never against the 4h trend.

Each V14 bot is the original strategy with its own class, parameters, stop, targets, time limit and engine, plus one
rule. Each runs next to its original as an A/B test.

## The HTF rule (`app/strategies/v14/htf.py`, fixed before any test)

The rule is a human trader's "trade with the bigger trend". It reads the coin's **1-hour candles**. The engine builds
4h and 1d candles from 1m bars and drops the whole candle on any 1-minute gap, so 1h is the robust source.

**The two trends:**
- **"4h" trend:** price vs EMA(84) of 1h closes (about the 4h EMA 21), and that EMA vs 12 hours earlier.
- **"Daily" trend:** price vs EMA(240) of 1h closes (about the daily EMA 10), and that EMA vs 24 hours earlier.

**Each trend scores +1, −1 or 0:**
- **up (+1):** price is above a **rising** average;
- **down (−1):** price is below a **falling** average;
- **flat (0):** anything else.

**The bias is the sum of the two scores:**
- **Long** only if bias ≥ +1: at least one higher timeframe agrees, and neither disagrees.
- **Short** only if bias ≤ −1.
- With fewer than 300 hourly candles, there is no trade.

**Filtering happens before ranking.** The scanners drop counter-trend set-ups before choosing their best candidates,
so a filtered coin never takes a slot from one that passes.

## Pre-registered study (`scripts/v14_htf_study.py`, `docs/V14_HTF_STUDY.json`)

- **Data:** 99 days of Bybit 1m bars from the research store. The first 14 days are warm-up; the rest is split into
  halves.
- **Comparison:** each strategy exactly as it runs live, once WITHOUT and once WITH the rule.
- **"HTF HELPS"** only if the HTF version's net R per trade beats the original in **both** halves.
- **Either way the bots run forward** on paper, each labelled with its verdict.

### Result (2026-10-04)

85 days after warm-up.

| Copy | Without the HTF rule | With the HTF rule | Verdict |
|---|---|---|---|
| V14.1 = V8.3 (6 coins) | 3,376 trades, −0.094 R (−0.105 / −0.084), −66.9 USDT | 1,463 trades, −0.120 R (−0.099 / −0.141), −36.1 USDT | does not help |
| V14.2 = V11.1 | 544 trades, −0.067 R (−0.053 / −0.100), −6.5 | 479 trades, −0.085 R (−0.079 / −0.100), −7.9 | does not help |
| V14.3 = V11.2 | 924 trades, −0.040 R (−0.070 / −0.007), −7.1 | 771 trades, −0.040 R (−0.086 / +0.012), −5.8 | does not help |
| V14.4 = V13 | 684 trades, −0.050 R (−0.073 / −0.026), −6.1 | **36 trades, +0.117 R (+0.055 / +0.187), +0.7** | **helps** |

R values are net R per trade (half 1 / half 2) and net USDT.

**Reading:**
- **For the snap-back and the two scanners, the rule mostly removes trades.** Each remaining trade is no better, and
  is worse in at least one half. Total losses only shrink when there are fewer trades.
- **For Snapback,** "buy a stretched dip only when the 4h / daily trend is up" turned it positive in both halves. But
  it leaves only 36 trades in 85 days, far too few to call it an edge. Live data will tell.

All nine bots run forward, as pre-registered. Freeze `4e87586fd10a17c6`.

## Program

| Item | Value |
|---|---|
| Files | `app/competition/v14_config.py`, `app/live/v14_worker.py`, `scripts/v14_freeze.py`, `docs/V14_FREEZE.json` |
| Database | `v14-forward.db` |
| Switch | `V14_FORWARD_ENABLED` |
| Warm-up | 14 days |
| Field | 9 CONTROL bots, no Jev twins |
| Names | each bot is named after its original + " HTF" (for example "Jinx HTF", "Bounce HTF") |
| QUALIFIED | ≥ 14 days, ≥ 100 trades, net > 0, PF ≥ 1.2, max drawdown ≤ 20% |
| Elimination | never |
| Live mirror | refuses it |
