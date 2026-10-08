# V6.2 / V6.6: what they do, how solid the edge is, and how to scale it

Read-only analysis, 2026-10-08. Paths are relative to `D:\Projects\BinanceBOt\paperlab`. Nothing in the repo was changed.

---

## 1. What the two bots do

### Shared V6 machinery (`app/strategies/v6/base.py`)

| item | value | where |
|---|---|---|
| Horizon | HOURLY: 1h signal, 4h + 1D context, **24 h time stop**. SWING: 4h signal, 1D context, 72 h time stop | `V6_CLASSES` L28-31 |
| Decision instant | H = signal candle close + 1 ms. It reads the coin's candles, `PositioningFeedV6.snapshot(H)` and `MarketContextV6.snapshot(H)` | `on_candle` L115-124 |
| Warm-up | at least 60 signal bars and 40 bars of the fast context timeframe | L36-37, L119 |
| Stop | structural extreme from the setup + 0.25 x ATR14 of the signal timeframe, as a % of price. **Refused if > 6%, raised to 1% if smaller** | L32-34, L131-137 |
| Target | **3R on the whole position**. No trail, no break-even, no partials | `TARGET_R=3.0` L35; `tps_r=[(3.0, 1.0)]`, `trail_atr=None, be_at_r=None` L157-158 |
| Time stop | `max_hold_s = time_stop_h*3600` (24 h hourly) | L162 |
| Positions | one per bot; default leverage 20 (a ceiling: the backtest uses `leverage_policy="needed"`) | L65-66; `scripts/backtest_2y.py` L299 |
| Cooldown | `cooldown_bars` x signal timeframe | L183-184 |
| Signal quality | mean of the setup's factors in [0,1]. It drives the sizing tiers | `app/strategies/v3/base.py` L151 |
| Trend definition | EMA20/50 on the slow EMA plus its 5-bar slope, and close vs the slow EMA. On 1D: EMA10/30 with a 3-bar slope | `ctx_trend` L80-83 -> `V3Strategy.trend` v3/base.py L95-111 |

Execution (`app/competition/v6_config.py`):
- Every fill is the open of the first 1m bar at or after H + 2 min (`EXECUTION_V6` L103-105, level-2 slippage).
- Bybit taker fee is 0.055% per side (`FEES_V6 = BYBIT_LINEAR` L106; V6_FREEZE `config.fees`). Funding is charged at the recorded settlements.

Sizing, `AGGRESSIVE_V6` / `SizingV6` (v6_config.py L116-122, L155-206):
- Base risk is **1% of the bot's equity**.
- If the exchange minimum order needs more than 1%:
  - 1.0-1.5% is taken only when quality >= 0.60 (LEGAL_STRONG).
  - 1.5-2.0% is taken only when quality >= 0.85 (LEGAL_CONVICTION). A CONTROL bot otherwise skips.
  - More than 2% is always skipped.
- Risk never goes above 2% (`max_risk_pct`).
- The bot halts at 30% below its peak (`health_state` L143-152). This rule was on in the backtest; only the engine's daily and strategy halts were off (backtest_2y L298).
- Starting book is 20 USDT.

### V6.6 "Market-Aligned Alt Trend" (`app/strategies/v6/p6_market_aligned.py`)

Parameters (L21-27, frozen in `docs/V6_FREEZE.json` params.V6.6): `min_breadth 0.60, high_bars 24, min_rel_strength 0.0, min_agg_oi -0.03, stop_bars 6, cooldown_bars 4`.

Entry, in `setup()` L50-92. All of these must hold on the closed 1h bar:
1. BTC's 1D trend is up or down, and **BTC's 4h trend equals it** (L56). The side follows BTC: long if up, short if down (L58).
2. ETH's 1D trend is not against the trade (L60).
3. Aggregate OI change over 24 h is >= -3% (no market-wide unwind; L60). It is notional-weighted over the breadth set (`v6_features.py` L280-298).
4. Breadth: the share of the 16-coin BREADTH SET closing above its 1D EMA20 is >= 0.60 for longs, or <= 0.40 for shorts (L62-64; `v6_features.py` L273-296).
5. The coin's own 4h trend (1D for SWING) equals BTC's direction (L65-67).
6. Relative strength: the coin's 24-bar return minus BTC's `ret_24h` is >= 0, in the trade's favour (L73-80).
7. Breakout: the bar closes above the prior 24-bar high (longs) or below the prior 24-bar low (shorts) (L81-89).
8. Stop anchor: the low (high) of the last 6 bars, then the base adds 0.25 ATR and clamps to 1-6%.
9. Quality factors: breadth, rel_strength, market_oi and market alignment (L90-92).

Positioning data V6.6 needs:
- **No per-coin positioning at all.** It never reads `pos`.
- It needs only the market context: BTC and ETH 1h closes, 1h closes of the 16 breadth coins, and their OI. These are the same for every traded coin.
- Funding is used only for costs and for the `expected_funding_pct` field in the signal's meta (base L143).

Minor asymmetry: in the backtest the context closes are capped at H - 1h (`backtest_2y.py` L163, `close:` offset `-HOUR`). The coin's own 24h return therefore ends at H while BTC's `ret_24h` ends an hour earlier. This is causal, just slightly mismatched.

### V6.2 "Momentum Continuation OI" (`app/strategies/v6/p2_momentum_oi.py`)

Parameters (L20-27): `impulse_bars 12, rank_min 0.85, min_oi_rise 0.02, min_cons 2, max_cons 6, max_retrace 0.5, cooldown_bars 3`.

Entry, in `setup()` L49-100:
1. The coin's 4h trend (ctx_fast) is up or down. The trade goes in that direction (L52-56). The coin's feed must exist (L53).
2. Impulse: for a consolidation of 2-6 bars, the 12-bar return that ended just before it ranks >= 0.85 among all past 12-bar returns in the trend direction (needs >= 60 history values; L58-72). On SWING, k = 6 bars of 4h.
3. **Open interest rose >= 2% across the impulse** (L74-77). It uses `oi_at`, which is capped at the decision watermark (base L91-96). No OI history means no trade, silently.
4. The consolidation retraced <= 50% of the impulse, and this bar closes beyond the consolidation's high (low) (L78-93).
5. Stop anchor: the extreme of the consolidation plus the signal bar, then the base adds 0.25 ATR, clamped 1-6%.
6. Quality factors: impulse rank, OI rise, shallowness, market alignment.
7. The first consolidation length whose impulse ranks high decides the outcome. If that one fails the OI or retrace test, the bot returns None (L77, L99) and does not try the next length.

Positioning data V6.2 needs:
- **The coin's own 1h OI series** (`positioning/<SYM>/oi_1h.csv`).
- Its funding settlements, for costs.
- The market context only for the alignment quality factor.

Lineage: V6.2 is V5.8 MOMENTUM_CONTINUATION_OI with fresh parameters (L39). In V5, V5.8 HOURLY was:
- +0.129R gross (p 0.13) on DEV 2025-03..2026-08;
- +0.102R gross (p 0.19, n 254) on the pseudo-holdout 2023-09..2025-02 (`docs/V5_RESULTS_FREEZE.md` L32, L67, L82).

That is directionally replicated but not significant.

---

## 2. Per-bot results, 2 years (docs/BACKTEST_2Y_SUMMARY.json)

20 USDT books, 1% base risk. "y1" is 2024-10..2025-09 and "y2" is 2025-10..2026-09. All amounts are in USDT.

| bot | ret % | net | y1 | y2 | trades | WR | PF | max DD % | fees | funding | ~net R/trade* |
|---|---|---|---|---|---|---|---|---|---|---|---|
| V6.6-XRP-1H | +24.5 | +4.89 | +3.99 | +0.90 | 81 | 48% | 1.52 | 7.3 | 0.85 | -0.09 | +0.27 |
| V6.6-ARB-1H | +19.8 | +3.96 | +3.14 | +0.82 | 67 | 48% | 1.53 | 7.2 | 0.51 | -0.06 | +0.27 |
| V6.6-ENA-1H | +13.5 | +2.70 | **-1.04** | +3.74 | 43 | 49% | 1.59 | 10.6 | 0.29 | -0.02 | +0.29 |
| V6.6-DOGE-1H | +2.1 | +0.42 | +0.33 | +0.09 | 79 | 37% | 1.05 | 11.3 | 0.63 | -0.07 | +0.03 |
| **V6.6, 4 bots** | **+15.0 avg** | **+11.97** | **+6.42** | **+5.55** | 270 | | | 7-11 | 2.27 | -0.25 | **~+0.21** |
| V6.2-ARB-1H | +18.8 | +3.75 | +3.20 | +0.55 | 48 | 48% | 1.71 | 7.7 | 0.42 | -0.04 | +0.36 |
| V6.2-DOGE-1H | +3.7 | +0.73 | +1.08 | -0.35 | 61 | 38% | 1.08 | 9.6 | 0.59 | -0.05 | +0.06 |
| V6.2-ENA-1H | +2.3 | +0.47 | +0.73 | -0.26 | 25 | 28% | 1.14 | 8.4 | 0.20 | -0.02 | +0.09 |
| V6.2-XRP-1H | -5.2 | -1.04 | -0.29 | -0.75 | 61 | 30% | 0.87 | 11.2 | 0.69 | -0.06 | -0.08 |
| **V6.2, 4 bots** | **+4.9 avg** | **+3.91** | **+4.71** | **-0.81** | 195 | | | 8-11 | 1.89 | -0.17 | **~+0.10** |

\* net / (about 1% of about 21 USDT equity) / trades. This is an approximation; the summary has no per-trade R. The server's result files (`<data-dir>/results/v6__*.json`) have `trade_list` with `r` per trade.

For reference, the field context:
- V6.1-XRP-4H (SWING) made +14.4% on 17 trades (PF 3.36, y1 +0.21 / y2 +2.67). The other V6.1 4H bots had 0-11 trades.
- Every faster program (V7, V8, V11-V14) lost 17-63%. Its fees were comparable to or larger than its net loss. For example, V8.3's 12 bots paid 58.7 USDT in fees and lost 69.2 USDT.

### Is the edge stable?

**V6.6 is stable at the family level, but not coin by coin.**
- Both years are positive in aggregate (+6.42 / +5.55).
- 7 of 8 coin-years are positive. Only ENA y1 lost.
- XRP and ARB earned 80% of their profit in year 1 (y2 about +0.8 to +0.9 each). ENA earned all of its profit in year 2. DOGE is about breakeven (PF 1.05, ~0.03R).
- Without the best coin (XRP) the family is still +7.08 over 189 trades.
- PF is 1.5-1.6 on three coins. With WR about 48% against a 3R target, the average winner is only about 1.65x the average loser. Most winners therefore leave on the 24 h time stop, not at 3R.
- Rough significance: pooled mean about 0.21R and a per-trade sd of about 1.3R over 270 trades give t ≈ 2.6. But:
  - trades across coins cluster in time, because all 4 share the same BTC/breadth gate, so the effective N is smaller;
  - V6.6 is the best of 7 V6 family-horizons that were run.

  Adjusted for both, the evidence is marginal-to-moderate, not conclusive.

**V6.2 is carried by one coin and one year.**
- ARB alone (+3.75) is 96% of the family's net (+3.91). Without ARB, the other three bots net +0.16 over 147 trades: zero.
- Year 2 is negative in aggregate (-0.81). 3 of 4 coins lost in y2.
- t ≈ 0.10R / (1.3/√195) ≈ 1.05, which is not distinguishable from zero.
- This matches its V5 lineage: about +0.1R gross, never significant.
- Treat V6.2 as **unproven**. Its "3/4 profitable" label comes from a strong ARB year 1.

Shared caveats, stated in the summary itself:
- The coins were chosen in 2026-09.
- The instrument rules are today's.
- The V6 families were written on 2026-09-24, after the whole backtest window.
- V6.2/V6.6 were singled out after seeing this backtest.

This is not an unseen test.

---

## 3. Why these two survive when the faster families did not

1. **Holding period vs fees: the main reason.**
   - A V6 trade risks at least a 1% stop and in practice about 2.5% (from the fees: V6.6 paid about 0.0084 USDT per trade; 0.0011 x 0.2 / fee implies an average stop of about 2.6%).
   - A round trip costs about 0.11% of notional plus slippage, so the cost is about **0.04R per trade**. Fees are about 16% of V6.6's gross (net 11.97, fees 2.27, funding 0.25, so gross about 14.5).
   - Scalpers with 0.5-1.2% stops pay 0.1-0.25R per trade and need a gross edge larger than that. None had it.
   - A 24 h hold lets a 3R target or a large time-stop exit happen. Over 2 years the bots made only about 34 trades per coin per year, so total cost stays small.
2. **Trend regime conditioning (V6.6).**
   - V6.6 trades only when BTC's 4h and 1D trends agree, ETH does not disagree, at least 60% of the breadth set is above its 1D EMA20, and aggregate OI is not unwinding.
   - It buys the relative-strength leaders' 24-bar breakouts in that direction.
   - This is classic time-series momentum plus cross-sectional momentum, filtered to market-wide trends. It is one of the few crypto effects with long out-of-sample literature support at 1h-1D horizons.
   - It is flat in chop. That is where the faster breakout and reversion families bled.
3. **Positioning confirmation (V6.2).** OI rising during the impulse means new money carried the move, not short covering. The idea is sound, but the evidence here is weak (see §2). V6.6 does not use per-coin positioning at all. Only its *market* OI gate is positioning-based.
4. **The exits fit the thesis.**
   - The stop sits beyond structure, not inside noise.
   - There is no break-even or partial exit that cuts winners. The V11 ladders turned 0.5-1.5R partials into fee-dominated small wins.
   - The time stop caps exposure to regime flips.
5. **What does not explain it.** Leverage does not: risk is 1%. Funding does not: it was a small cost of -0.25.

---

## 4. Scaling options, ranked by expected benefit vs risk

General rule for honest validation:
- Freeze the V6 sources and parameters exactly as `docs/V6_FREEZE.json` (fingerprint 93f9c48b67163c69). No parameter is touched.
- Pre-register a JSON before any run: coin list by an objective rule, windows, metrics, pass criteria.
- Treat DEV = year 1 (2024-10..2025-09) as a first look only. TEST = year 2 (2025-10..2026-09) decides.
- Use an untouched holdout (see below).

**Which windows are untouched?**
- For the 4 original coins, 2024-10..2026-09 is seen.
- For the V6.2 hypothesis, V5 already used Bybit 2023-09..2025-02 and 2025-03..2026-08 (`docs/V5_PROTOCOL.md` §2).
- **No historical window is fully clean for V6.2.**
- For V6.6, which was new in V6, Bybit **2023-10..2024-09** has not been used by this rule. It is the best holdout year, but it needs a new fetch: 1m, funding and OI for 2023-07..2024-09, which is a network action for the user to approve. Only about 16 of the 30 coins were listed then.
- The only fully untouched data is the **forward** period from 2026-10 onward. The live V6 arena already runs these exact rules on ARB/ENA/XRP/DOGE.

### Rank 1: (a) Unchanged rules on the 26 other coins (coin-out-of-sample)

Benefit:
- This is the decisive test.
- If it passes, every other scaling option rests on 5-7x more trades and a credible edge.
- If it fails, V6.6's 4-coin result was selection plus luck, and scaling would only amplify noise.
- Cost and risk are near zero: no code or parameter change, about 52 single-coin replays.

What must be true for it to raise returns:
- The edge is a property of the rule (market-wide trend plus leader breakout), not of ARB/XRP.
- Expect shrinkage: DOGE at 0.03R is a realistic "typical coin" outcome.

Caveat: coin-OOS is **not** time-OOS.
- V6.6's gate is the same BTC/breadth regime for every coin, so new coins trade in the same market episodes the 4-coin backtest already saw.
- A good 2024-26 trend regime would lift all coins together.
- That is why the time holdout below is still needed.

Pre-registration specifics:
- Coins: the 26 listed, minus BTC.
  - For BTC, V6.6's relative-strength filter is BTC versus itself, about 0, which passes `min_rel_strength = 0`. That is degenerate.
  - BTC is also illegal at 20 USDT (minimum order about 78 USDT).
- Optionally apply the frozen V6 universe rule's objective gate instead, "listed >= 90 days" (`docs/V6_FREEZE.json` `universe.rule`), so new listings do not trade in their first weeks.
- Report legality skips separately. At 20 USDT, `SizingV6` will skip many setups on coins with a high minimum order:

  | coin | minimum order (USDT, from `docs/V6_UNIVERSE.json`) |
  |---|---|
  | ETH | 24.8 |
  | ZEC | 11.2 |
  | SOL | 10.3 |
  | SUI | 7.6 |
  | BNB | 7.2 |
  | LTC | 5.3 |
  | XMR | 5.2 |

- Run each family twice:
  - at **20 USDT**, the live-legal result;
  - at **1000 USDT** (`strategy_starting_balance`), which measures the rule's edge without the minimum-notional filter.
- Pre-registered primary metric: pooled net R per trade per family over TEST (year 2), with a **block bootstrap by week** (trades cluster). Proposed pass for V6.6:
  - pooled TEST net expectancy > 0 with p < 0.05;
  - the DEV sign also > 0;
  - at least 60% of coins with >= 10 trades are net positive;
  - no single coin contributes > 30% of the pooled net.
- V6.2 gets the same test. Given §2, expect it to fail.
- Listing-date coverage (from `docs/V6_UNIVERSE.json` `launch_ts`):
  - **No year-1 data**: FARTCOIN (2024-12), HYPE (2024-12), TRUMP (2025-01), PUMPFUN (2025-07), XPL (2025-08), LIT (2025-12).
  - **Not in the V6 pool; dates unknown**: AKE, USELESS. Expect them to be recent.
  - Count coin-years, not coins.

### Rank 2: (c) One shared account for every qualifying coin-bot

Benefit (largest lever after (a)):
- Return per unit of capital scales with the number of coin-bots trading off one equity.
- With 20 coins instead of 1, the trade count per unit of capital is about 20x before overlap.
- A bigger shared book (for example 200-1000 USDT) also removes most minimum-notional skips, so every setup gets its full 1%.

What must be true:
- The per-coin edges hold (that is (a)).
- Concurrent positions are not so correlated that one market reversal gives back months of profit.
- **This is the main risk.** V6.6 can only fire when the whole market trends one way. In a strong trend, 5-10 coins can break out within the same few hours, all on the same side. One BTC reversal then stops them all together: 5-10 x 1% hits at once.

Required design, pre-registered:
- Per-trade risk is 1% of *total* equity.
- Cap total open risk at about 4-6%, and cap same-direction risk. The tie-break when the cap binds must be pre-registered: first-come, or the highest `signal_quality`.

How to validate cheaply:
- Post-process the per-coin `trade_list`s from the (a) study. Each has entry/exit timestamps and `r`.
- Simulate one equity with the concurrency cap, and report max DD, worst day and worst week.
- Then confirm with a true multi-symbol replay.

### Rank 3: (d) Add the 4h / SWING horizon

Benefit is small but cheap:
- `for_class("SWING")` already works for both families (base L28-31, L69-77).
- In SWING mode, V6.2 uses a 6-bar (24 h) impulse; V6.6 uses its own 1D trend, a 24 x 4h (4-day) breakout and a 6-bar 24 h relative-strength window.
- They have **never been run as SWING**: `SWING_FAMILIES = ("V6.1",)`, v6_config L34.
- Expect roughly 5-10 trades per coin per year. Each trade costs a smaller share of R in fees but pays about 3x the funding over a 72 h hold.
- The only SWING evidence so far:
  - V6.1-XRP-4H: +14% on 17 trades;
  - V5.4 SWING: the only V5 hypothesis that passed its holdout, at +0.22R gross.
- Mostly a diversifier: a different time scale and stop width, and less clustering.

What must be true: the trend premium exists at the 1-4 day scale for these coins, net of 72 h funding.

Validate: same pre-registered DEV/TEST/holdout design, as separate family-horizons, never merged with HOURLY evidence.

### Rank 4: (b) Higher risk per trade, 1% -> 2-3%

Benefit: purely mechanical. It is the largest return boost and the highest risk.
- To first order, return and drawdown both scale linearly with risk. Compounding drag is small at these sizes.
- Observed max DD at 1% is **7-11%** (V6.6 7.2-11.3; V6.2 7.7-11.2).

| risk | approx. 2-y return, V6.6 avg | approx. max DD (observed path) | notes |
|---|---|---|---|
| 1% | +15% | 7-11% | as backtested |
| 2% | about +30% | about 14-23% | the current `max_risk_pct` cap |
| 3% | about +45% | about 21-34% | DOGE's path (11.3% -> about 34%) would **trip the live 30% halt** |

- A 4-bot, 2-year sample understates future drawdown. At a 52-63% loss rate (V6.6 WR 37-49%), a 10-loss streak somewhere in about 70 trades has single-digit to about 20% odds. That is about 20% DD at 2% risk and about 26% at 3%.
- Risk/return per unit of drawdown does **not** improve. Raising risk only moves along the same line and multiplies the estimation error in a 0.1-0.2R edge.
- Kelly view: with edge about 0.21R and per-trade variance about 1.7R², full Kelly is about 12%. If the true edge is 0.05-0.1R, quarter-Kelly is about 1-1.5%. Given the uncertainty, 1% is about right, and 2% is the defensible maximum *after* (a), (c) and forward evidence.
- Side effect: at 2% base risk, fewer setups are minimum-notional-limited, so the **trade set changes**, adding wider-stop trades. It must be re-run, not just scaled.
- Any change to `AGGRESSIVE_V6` is a V6 re-freeze, which means a new experiment.

What must be true: the edge is real and stable (the evidence above), and drawdown tolerance exceeds about 2x the observed.

Validate: Monte Carlo or a block bootstrap of the TEST and holdout trade lists at 1/1.5/2/3%, reporting P(DD > 30%) and P(halt). Only then decide.

### Not recommended

Tuning thresholds per coin, picking coins by backtest PnL, or relaxing V6.6's gates to get more trades. Each turns a replication into a fit.

---

## 5. Realistic ceiling of average daily return

Baseline, observed:
- V6.6 per bot is +15% over 2 years, ≈ +7.2%/yr compounded ≈ **0.019%/day**.
- Cross-check: 34 trades/yr x 0.21R x 1% ≈ 7.1%/yr. ✓

Scaled portfolio, one account, risk r per trade of total equity:

  annual R = coins x trades/coin/yr x capture-after-concurrency-cap x net R per trade

Daily return is computed as ln(1+annual)/365. Compounded log-growth per trade ≈ r·μ − ½(r·σ)², with σ ≈ 1.3R.

| scenario | coins | trades/coin/yr | capture | trades/yr | net R/trade | risk r | log-growth/yr | ≈ daily |
|---|---|---|---|---|---|---|---|---|
| Edge fails OOS | 20 | 34 | 0.6 | 408 | 0.00 | 1% | ≈ -0.03 | ≈ -0.01%/day |
| Central, haircut edge | 20 | 34 | 0.6 | 408 | 0.08 | 1% | 408 x (0.0008 - 0.0000845) = 0.29 | **≈ 0.08%/day** (+34%/yr) |
| Central at 2% | 20 | 34 | 0.6 | 408 | 0.08 | 2% | 408 x (0.0016 - 0.000338) = 0.51 | **≈ 0.14%/day** (+67%/yr) |
| Optimistic: in-sample edge holds; V6.6 + SWING | 20 | 40 | 0.6 | 480 | 0.20 | 1% | 480 x (0.002 - 0.0000845) = 0.92 | ≈ 0.25%/day (+150%/yr) |
| Optimistic at 2% | 20 | 40 | 0.6 | 480 | 0.20 | 2% | 480 x (0.004 - 0.000338) = 1.76 | ≈ 0.48%/day (about 5.8x/yr) |

Notes on the scenarios:
- **Edge fails OOS**: costs are already inside net R; the slight negative is from drag.
- **Central**: 0.08R is about 40% of the in-sample 0.21R. That is a typical shrinkage for a best-of-7 selection.
- **Optimistic**: drawdowns also scale with concurrency. At 2%, a 5-position correlated reversal costs about 10% in hours.

Reading:
- A **realistic planning range is about 0.05-0.15%/day**: roughly +20% to +70% a year.
- That holds only if the coin-OOS and time-holdout tests pass and a correlated-risk cap is in place.
- About 0.25-0.5%/day is the "everything holds" ceiling. Project history (V3-V5, V8-V15) says in-sample edges rarely survive whole.

**Why 1%/day is not reachable:**
- 1%/day needs about 3.6 log-units a year.
- At 2% risk and 0.2R, that is about 1,000 *uncorrelated* winning-edge trades a year.
- 1h/4h trend signals on 30 coins, which cluster in the same market episodes, cannot supply that.

---

## 6. Implementation notes for a study that reuses scripts/backtest_2y.py

How the script builds and runs jobs:
- `jobs(only)` (L249-276) takes the V6 coins from `freeze("v6")["universe"]["traded"]` (only ARB/ENA/XRP/DOGE) and `v6_config.field_plan(coins, jev=False)`. That plan gives all 6 HOURLY families plus V6.1 SWING (v6_config L71-81).
- A job is a plain dict: `{"program":"v6","key":"V6.6-SOL-1H","strategy_id":"V6.6","role":"CONTROL","coin":"SOL","symbol":"SOLUSDT","timeframe":"1h","horizon":"HOURLY","multi":False}`.
  - For SWING: `"timeframe":"4h","horizon":"SWING"`, key suffix `-4H`. You can also build these with `BotSpecV6(sid, coin, horizon)` (v6_config L39-68).
  - A study should build its own list, either a new script that imports these functions or an `--only`-style filter for V6.2/V6.6 x new coins. Do not change `jobs()`.
- `build(job, root)`, v6 branch L304-317:
  - `positioning(root, [sym], breadth_set, intervals, START-WARM_SINGLE, END)` (L153-169) builds the coin's `PositioningFeedV6` and the shared `MarketContextV6`. It uses the live watermark offsets: funding/oi/ratio at H, premium and context closes at H-1h.
  - Then `load_v6()[sid].for_class(horizon, feed, ctx)`, and a thin `ReplayEngine` with `settings_v6()` (halts off), `EXECUTION_V6`, funding from the archive, and `sizing_v6()` (`SizingV6`, keeps the live 30% halt unless `BT_VARIANT=nohalt`).
- **Blocker 1, instrument rules.** `rules_all = man["rules"]` comes from `docs/V6_FREEZE.json`, which has rules for **only the 4 traded coins**, so `rules_all[sym]` raises KeyError for a new coin. Use the 30-coin rules in `docs/V11_FREEZE.json` (or V13/V14 freeze, or `<data-dir>/bybit_rules.json`). They are the same Bybit snapshot.
- **Funding intervals.** `intervals` come from the V6 freeze's `funding_interval_min`, which covers only the 16 breadth coins. The default is 480 min. The feed infers the interval from settlements once 2 are visible, so this mainly affects the first hours. Still, use V11's `funding_interval_min`: AKE, FARTCOIN, LIT, ONDO, PUMPFUN, TAO, TRUMP, USELESS and XPL are on 4 h intervals.
- **Keep the breadth set fixed** at the V6 freeze's 16 (`man["breadth_set"]`). It is part of V6.6's rule. 10 of the 26 new coins are in it (SOL, ZEC, HYPE, NEAR, ADA, SUI, 1000PEPE, PUMPFUN, UNI, LINK) as well as BTC/ETH. That is causal and allowed, but note the mild self-reference.
- `run_one` (L466-490) streams the 1m tape from `archive/klines/<SYM>/<SYM>-1m-<YYYY-MM>.zip`, starting 92 days early (`WARM_SINGLE`), then `eng.run(cls, bars, since_ms=START, leverage=20, signal_tf=..., only_symbol=sym)`.
- `summarize` (L423-459) writes:
  - return, PF and DD;
  - the y1/y2 split at `MID`, plus `exits`, `coins`, `monthly` and `rejects`;
  - **`trade_list`** (entry, exit, net, fees, funding, r, kind): enough for the portfolio and Monte Carlo post-processing in (b) and (c) without a re-replay.
- Isolation and windows:
  - `BT_VARIANT=<name>` writes to `results_<name>/`, so it never mixes with the published results. Only `nohalt` changes behaviour.
  - `BT_START`, `BT_END` and `BT_MID` set the DEV/TEST/holdout windows (L57-58). `BT_WARM_SINGLE_DAYS` sets the warm-up.
  - `report` writes `report_<variant>.json`.
- **Silent-failure risk.** `read_series` (`app/backtest/bybit_archive.py` L171-183) returns `[]` when `positioning/<SYM>/oi_1h.csv` is missing. V6.2 then never trades (`oi_at` is None, p2 L76-77) and records 0 trades, not an error. The study must assert that each coin's OI, premium and funding series are non-empty over [START-warm, END] before trusting a zero-trade result. V6.6 needs no per-coin positioning.

Data each bot needs:

| bot | needs |
|---|---|
| V6.6 | 1m tape for the coin, funding (costs), the breadth set's 1h closes (from `prep`) / OI / funding. Every coin has this once `prep` has run for the breadth set, which the published run did. |
| V6.2 | the above plus the coin's **1h OI**. Premium and ratio are only recorded in meta. |

Coverage of the 26 extra coins:
- `scripts/v5_bybit.py fetch` (L87-145) writes `oi`, `ratio`, `premium`, `mark` and `index` 1h series for every symbol in `universe.json`. If the 30-coin `/data/bt2y` archive was built by it, as the brief says, then all 26 have them for 2024-07..2026-09.
- `docs/V6_UNIVERSE.json` shows `data: {tape, funding, open_interest: True}` for every listed coin.
- I could not verify the server files from here. Check with a one-line count of rows per `positioning/<SYM>/oi_1h.csv` first.
- Coins listed inside the window start trading about 1-2 weeks after listing (60 x 1h and 40 x 4h bars; V6.2 needs 60 impulse-history values). The 90-day-listing rule is recommended.

Compute: 25 coins x 2 families = 50 HOURLY jobs, plus 50 SWING if (d) is included. They are single-symbol 1m replays of about 2.25 years each, with the same per-job cost as the published V6 runs. They run with `--workers N` on the server.
