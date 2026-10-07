# V4 INTRADAY SPECIALISTS — protocol

Written before any V4 bot was replayed. V1, V2, V3 and V3.1 are frozen and untouched; V4 has its own
strategies (`app/strategies/v4/`), expected-edge model (`app/competition/v4_edge.py`), Jev policy
(`JEV_POLICY_V4`, `app/ai/jev/v4.py`), tables (`v4_runs`, `v4_bots`, schema 14), runner
(`scripts/run_v4_arena.py`) and documents. The root-cause analysis it answers is `docs/V4_ROOT_CAUSE.md`.

## 1. Goal

Aggressive, cost-efficient intraday specialists for a **20 USDT** Bybit linear account. Aggressive means
participating in every setup that clears the checks and sizing up only when justified — not tiny scalps,
not leverage to hide a weak strategy. A small-timeframe trigger times an entry into a larger structural move:

    1h trend  ->  15m structure (30m for 30m bots)  ->  3m / 5m / 15m / 30m trigger  ->  hold 30-240 min

## 2. Strategy families (one market hypothesis each)

| id | family | hypothesis |
|---|---|---|
| V4.1 | TREND_PULLBACK | a pullback to value inside a 1h trend that resumes carries the trend's drift for hours |
| V4.2 | SQUEEZE_EXPANSION | the first expansion out of a genuinely compressed range, with the 1h trend, runs |
| V4.3 | BREAKOUT_RETEST | a broken level that is retested and holds has flipped; the retest gives a stop right beyond it |
| V4.4 | MOMENTUM_CONTINUATION | a top-decile structure impulse with the 1h trend persists; the flag break times it |
| V4.5 | FAILED_BREAKOUT_REVERSAL | a pierce of the range extreme that closes back inside traps breakout traders (only when the 1h trend does not support the breakout) |
| V4.6 | TREND_RANGE_REJECTION | in a 1h trend, a rejected probe of the range edge on the trend's side marks value being defended |
| V4.7 | FLOW_BREAKOUT | a breakout on abnormal volume with taker flow in its direction is real participation and continues |

Seven families, no parameter grid, no indicator-combination bots. Every family except V4.5 trades only in
the direction of the 1h EMA trend (20/50 + slope). A 3m / 5m bot enters on the first trigger bar that resumes
in the setup's direction within one structure bar of the setup (each setup traded at most once); a 15m / 30m
bot enters on the setup bar's close.

**One exit for every family** (the exit analysis found nothing to fix): structural stop clamped to 0.6%–2.5%
(the frozen fee gate needs >= 0.44%), 50% at +1.5R, break-even once +1.5R is reached, the rest trailed at
2 x ATR(structure timeframe), maximum hold 180 min (3m / 5m) or 240 min (15m / 30m).

## 3. Timeframes and the research allocation

3m 15% · 5m 20% · 15m 35% · 30m 30% of the CONTROL bots: 7 families x (3 + 4 + 7 + 6) coins = **140**
CONTROL bots. This is where research effort goes, never a qualification bias: every bot faces the same gates.
The faster timeframes trade the best-scored coins (they pay the costs more often).

## 4. Coin selection — TRADEABILITY SCORE (never PnL)

`app/competition/tradeability.py`, rule fingerprint recorded in every run. Per window, from the 30 days
**before** the window's first trading day: candidates = the 40 Bybit linear USDT perpetuals with the highest
median daily turnover, listed >= 60 days before the window. Gates: Binance 1m tape for every window month;
turnover >= 20 M USDT/day; half spread (tick/2) <= 2 bps; median 30m range >= 30 bps; round-trip taker cost
<= 35% of the median 30m range; a 1%-risk position at a typical stop (one median 30m range, 0.6%–2.5%) must
clear the exchange minimum value and quantity with a quantity step <= 25% of the order and <= 20x leverage.
Score = mean percentile rank of turnover, cost ratio and step granularity. Universe = the top 10.
The rule is frozen; the coin list is recomputed per window (`docs/V4_UNIVERSE_DEV.json`,
`docs/V4_UNIVERSE_TEST.json`). Stated limits: candidates come from today's instrument list (survivorship),
and today's tick / step filters are applied.

DEVELOPMENT universe (scoring 2025-10-02..10-31): HYPE, ENA, FARTCOIN, TAO, APT, DOGE, XPL, WLD, PENGU, ARB.

## 5. Pipeline of every bot

    setup -> legality + cost (RiskManager; round trip <= 25% of R)
          -> EXPECTED-EDGE gate
          -> sizing: TAKE 1.0% of equity at risk; CONTROL's deterministic ATTACK 2.0%
          -> [Jev V4] -> execution (Bybit fees 0.055% taker, spread + impact model, Bybit filters, funding)

**Expected-edge model** (`v4_edge.py`): one cell per family x timeframe, pooled over the universe's coins;
evidence = the RAW observation ledger of the SAME window (every legal setup simulated at TAKE size and
sequenced like an ungated bot), used causally (a candidate at t sees only outcomes that exited before t).
mu = sum(gross R of the last 300 resolved setups) / (n + 30); TAKE needs n >= 30 and mu - cost_R >= +0.02R;
CONTROL's ATTACK needs (mu - cost_R - se) >= +0.05R and a healthy bot. The same model runs in DEVELOPMENT
and TEST; no outcome of one window ever reaches the other.

**AGGRESSIVE_V4**: TAKE 1.0%, ATTACK 2.0%, 20x leverage ceiling; no ATTACK at >= 18% below peak or when the
last 20 trades' expectancy < -0.25R; halt at 30% below peak. No size is ever cut below TAKE.

## 6. Jev V4

Asked only about a setup that passed legality, cost and expected-edge checks: *"Given that this setup
already passed legality, cost and expected-edge checks, do current market conditions CONTRADICT, SUPPORT or
STRONGLY SUPPORT the setup?"* CONTRADICT -> SKIP, SUPPORT -> TAKE (1.0%), STRONGLY SUPPORT -> ATTACK (2.0%,
needs a healthy bot and an order the RiskManager accepts, otherwise TAKE, recorded). A failed request is SKIP.

Every Jev bot is compared with its matched CONTROL, ALWAYS-TAKE, ALWAYS-SKIP (0) and 20 RANDOM twins that
draw SKIP / TAKE / ATTACK with **the Jev bot's own action distribution** through the same rules.
**Selection alpha** = Jev net - median random net. Jev must beat the random 90th percentile. The ATTACK test
reports ATTACK count, win rate, expectancy and the incremental PnL vs the same trades at normal size.

## 7. Account size

The competition is 20 USDT. The field's CONTROLs are also replayed at 50 and 100 USDT as a diagnostic only,
to separate BAD (loses at every size) from ACCOUNT_SIZE_CONSTRAINED (the 20 USDT book was limited by exchange
minimums while a bigger book of the same strategy was net-positive with a raw edge). A 50 / 100 USDT result
never qualifies a 20 USDT bot.

## 8. Gates (every one must pass; pre-registered)

adequate sample >= 30 trades; participation (3m 0.5, 5m 0.5, 15m 0.4, 30m 0.25 trades/day); **raw edge:
gross expectancy > 0R before any cost**; net PnL > 0; net expectancy > 0R; PF >= 1.10; bootstrap
P(mean net R <= 0) <= 10%; max drawdown <= 30% and never halted; no liquidation; net > 0 without the best 3
trades and the top 3 <= 60% of gross profit. Jev bots additionally: skip rate <= 80%; beat the matched random
90th percentile with positive selection alpha; beat the CONTROL by >= +0.50 USDT; AUC 95% CI above 0.50;
error rate <= 2%.

## 9. No overfitting

DEVELOPMENT (2025-11-01..2026-04-30, warm-up 2025-10) -> analyzer -> changes -> **freeze V4**
(`docs/V4_FREEZE.md`) -> pre-register the HOLDOUT (`docs/V4_TEST_PREREGISTRATION.json`) BEFORE any holdout month
is downloaded -> TEST -> no modification. ADVANCED = passes every gate in BOTH windows. If TEST fails, the
answer is a V5 hypothesis — V4 is never patched.

**Holdout**: 2025-04-01..2025-09-30 (warm-up 2025-03). 2026-05..08 was consumed by the V3.1 TEST and 2026-09
is not complete, so the fresh six months are the ones before every V3 / V3.1 / V4 window. No V3, V3.1 or V4
design decision used any data from 2025-03..2025-09 (the V3 archive starts 2025-10). The holdout universe is
recomputed by the frozen tradeability rule from 2025-03 data, so coins that were not tradeable then
(e.g. contracts listed later) are not in it.

## 10. What is NOT allowed

Loosening a gate, hiding a losing bot, removing fees, lowering slippage, using future data, tuning on TEST,
choosing coins by their historical PnL, or raising leverage to hide a weak strategy. If no bot passes both
windows the result is reported exactly: **ADVANCED SET = NONE**.
