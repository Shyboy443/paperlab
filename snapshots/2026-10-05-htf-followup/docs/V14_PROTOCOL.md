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

## Revised HTF rule (2026-10-05, `V14_HTF_PAPER_V2`)

The old rule used hourly EMA(84/240) proxies and compared both with the lower-timeframe entry price. Those are not
EMA(21/10) on actual 4h/daily closes. A dip at entry could erase an otherwise completed bullish HTF trend. It also
lacked an explicit decision cutoff and accepted missing/stale hourly history by candle count alone.

The revised rule assembles **actual OHLC 4h and daily candles** from exact groups of 4/24 closed hourly candles,
aligned to UTC. Incomplete groups are discarded. Bars closing after the decision and forming bars are excluded.
The latest completed UTC hour must be present, with 42 consecutive complete 4h bars and 20 consecutive daily bars.
No price imputation bridges a gap. An old missing hour can therefore block entries until enough continuous history
has rebuilt; this is intentional and is counted as `missing_or_stale_htf`.

Each timeframe compares **its own last completed close** with its own EMA: 21 periods on 4h, 10 on daily. EMA slope
is measured over 3/1 HTF bars. Price distance and slope are normalized by that timeframe's trailing ATR14 (simple
mean true range). Up/down requires a distance beyond 0.10 ATR and slope beyond 0.05 ATR per bar. Anything else is
neutral, including transitions; neutral alone is not evidence of a tradable range. The entry price cannot rewrite
the HTF state. These small deadbands are engineering defaults, not parameters fitted to the historical results.

| Family | Long rule (short is symmetric) |
|---|---|
| V14.2 breakout | 4h up; daily cannot be down |
| V14.3 pullback | Daily cannot be down; 4h up, or neutral 4h inside daily up with slope >= -0.05 ATR/bar |
| V14.1 / V14.4 reversion | Neither HTF down; at least one up; 4h slope >= -0.20 ATR/bar |

The copied strategy still supplies the lower-timeframe setup, entry confirmation, stop, targets and position rules.
An initial prototype allowed both-neutral reversion entries; its VWAP discovery-half result was worse and total
losses increased. That prototype and its six-coin results are archived under `snapshots/2026-10-05-htf-audit`.
The final rule retains directional context. This revision is exploratory; prior study data is not an unseen holdout.

**Filtering happens before ranking.** Every accepted signal carries its HTF closes and policy. The V14 engines also
recheck the regime before executing a queued market or resting limit order, using only candles completed before
the fill bar opens. A changed regime or stale feed cancels the order. Signal and fill contexts are preserved in the
entry fill metadata. Existing exits remain active regardless of the entry filter.

The HTF view is cached per coin/hour and refreshed on a new hourly candle. Histories are bounded by the existing
600-hour context capacity. Warm-up increases to 25 days. A new freeze identity separates V2 paper results from V1.
Closed-candle handling matters because Bybit reports the last traded price as `closePrice` for an unclosed kline.
See [Bybit's official kline documentation](https://bybit-exchange.github.io/docs/v5/market/kline).

## Pre-registered study (`scripts/v14_htf_study.py`, `docs/V14_HTF_STUDY.json`)

- **Data:** 99 days of Bybit 1m bars from the research store. The first 14 days are warm-up; the rest is split into
  halves.
- **Comparison:** each strategy exactly as it runs live, once WITHOUT and once WITH the rule.
- **"HTF HELPS"** only if the HTF version's net R per trade beats the original in **both** halves.
- **Either way the bots run forward** on paper, each labelled with its verdict.

### Historical V1 result (2026-10-04; does not evaluate the revised rule)

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

All nine original V1 bots ran forward as pre-registered. The table above predates the execution corrections and
the HTF revision. Its study script also did not pass a funding schedule despite its description; do not interpret
those historical values as funding-inclusive results for V2.

## V2 validation

`scripts/v14_htf_revision_study.py` compares the copied strategy alone (BASE), archived hourly-proxy filter (OLD),
and final completed-candle filter with fill checks (NEW). All arms use the same 25-day warm-up, fees, recorded
funding, sizing, execution engine costs, 60.001-second latency and daily book resets. Reset and final closing trades
and their costs are included. Funding is attributed to each position and included in reported net R and net USDT.
`net_profit_factor` uses net USDT, while legacy `pf` uses R. Final results and source hashes are saved in
`docs/V14_HTF_REVISION_STUDY.json`; the audit explains the outcome in `docs/HTF_LOGIC_AUDIT.md`.

A consistent improvement requires better mean net R in both chronological halves. This criterion does not establish
profitability, a statistically proven edge, or future returns. The same historical sample was used in prior studies;
forward qualification remains necessary. Prior V1 verdicts are not inherited by the V2 freeze.

## Program

| Item | Value |
|---|---|
| Files | `app/competition/v14_config.py`, `app/live/v14_worker.py`, `scripts/v14_freeze.py`, `docs/V14_FREEZE.json` |
| Database | `v14-forward.db` |
| Switch | `V14_FORWARD_ENABLED` |
| Warm-up | 25 days |
| Field | 9 CONTROL bots, no Jev twins |
| Names | each bot is named after its original + " HTF" (for example "Jinx HTF", "Bounce HTF") |
| QUALIFIED | ≥ 14 days, ≥ 100 trades, net > 0, PF ≥ 1.2, max drawdown ≤ 20% |
| Elimination | never |
| Live mirror | refuses it |
