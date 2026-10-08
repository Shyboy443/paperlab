# PaperLab: candidate families for a higher validated return (design, 2026-10-08)

Read-only design. No repo file was changed. Code facts below were checked in `D:\Projects\BinanceBOt\paperlab`.

## 0. Start here: the honest frame

**0.5-1%/day is not a target any validated strategy can meet.**
- 0.5%/day compounds to about 500%/yr.
- Published trend and carry systems in crypto report a Sharpe of roughly 0.8-1.5 before decay. At 30% annual volatility, that is 25-45%/yr, or 0.07-0.12%/day, and that is the published best case.
- Daily return is mostly a **choice of risk**. Any of the candidates below can be levered 2x for 2x the return and roughly 2x the drawdown.
- So the ranking uses return per unit of risk (Sharpe, return/maxDD) at a fixed risk budget. It also states what each candidate would return at the lab's 1%-risk convention.

**Where the lab stands** (`docs/BACKTEST_2Y_SUMMARY.json`, 2024-10..2026-10, 20 USDT books, Bybit costs, recorded funding):

| family | 4 bots (XRP, ARB, ENA, DOGE) | mean 2-yr return | trades/bot | note |
|---|---|---:|---:|---|
| V6.6 market-aligned alt trend, 1h | +24.5, +19.8, +13.5, +2.1 % | +15.0 % | 43-81 (2-3/month) | PF 1.05-1.59, maxDD 7-11 % |
| V6.2 momentum + OI, 1h | +18.8, +3.7, +2.3, -5.2 % | +4.9 % | 25-61 | year 2 weaker than year 1 |

- That is about +7%/yr, or 0.02%/day, per bot.
- **Selection bias:** these 2 families are the survivors of 6 V6 families (V6.1/3/4/5 lost) and dozens of earlier versions. Forward expectation should be shrunk well below +15%/2yr.

**Prior in-house failures that constrain this design.** Read these before building anything.
1. **`research/cross_sectional_momentum`** (Binance top-50 perps, weekly, 63-day rank, vol-scaled, 2-ATR ratcheting stops, OOS 2022-2024): FAILED.
   - Cross-sectional (CS) long-only: -0.45%/month. CS long-short: -0.56%/month. Positive cells: 0/9 for both.
   - Time-series momentum baseline: **-85% OOS**, average hold 4.7 days. The stop design (20-day high minus 2 ATR, no intraweek re-entry) cut a 63-day signal into 3-5-day trades and churned it.
   - The CS books were tiny (mean gross exposure 5%) because the 5% weight cap bound.
2. **`research/regime_filtered_crypto`** (63-day momentum + BTC>200DMA + breadth gate + spot-hedged funding sleeve, OOS 2022-2025): FAILED at -0.45%/month.
   - The funding sleeve lost $16k on fees and slippage against $4k of carry.
   - In-house break-even: the hedge needs about 16 days of threshold-level funding just to repay its legs.
3. **`docs/SYSTEM_REVIEW.md` / `ALTDATA_STUDY` / `ML_VS_SIMPLE_STUDY`** (5 minutes to 4 hours):
   - Funding, open interest, long/short ratio and taker imbalance add **no robust information** beyond price.
   - The only robust effect is **short-term cross-sectional reversal**, worth 0-5 bp gross against about 11 bp taker cost.
4. Every 5m-15m family lost (V7, V8, V9, V11-V15), mainly on costs.

**Design consequences:**
- Daily or slower signals only, where expected moves are 5-30% and the 0.11% round-trip taker cost is under 2% of the move.
- Exits wide enough not to destroy the holding period.
- Liquid majors rather than a top-50 tail.
- Funding counted explicitly: a trend-long pays about 10-15%/yr of notional in bull markets.
- Every new family is judged against the lab's own failed baselines, not against zero.

## 1. Cost and data facts used below

- **Fees:** Bybit linear taker 0.055%/side and maker 0.02% (`FEES_V6 = BYBIT_LINEAR`), so about 0.11% taker round trip plus 1-5 bp spread/slippage. `EXECUTION_V6` fills at the first 1m open after H+2 min.
- **Funding:** at the base 0.01%/8h a long pays 0.03%/day (about 11%/yr) of notional. In euphoric phases alts pay 0.05-0.1%/8h (55-110%/yr). This matters for any multi-week long.
- **Sizing:** `SizingV6` puts 1% equity at risk on the stop distance, with 1.5% and 2% legal tiers, and SKIPs above that. Leverage ceiling 20; engine notional cap 8x equity.
  - A daily-bar stop of 10-15% at 1% risk on a **20 USDT** book is about 1.5-2 USDT notional, **below Bybit minimum order sizes**. Every daily candidate must therefore be studied and paper-run on a **1,000 USDT** book, or with a higher risk fraction. BTC already cannot be traded legally on 20 USDT (memory note).
- **Engine:**
  - `ReplayEngine` holds one position per bot and sizes by stop. `V3Strategy.entry()` already supports `trail_atr` (TrailSpec "atr" on the signal timeframe), but there is no resize or rebalance of an open position.
  - `V6Strategy` accepts only HOURLY/SWING classes and refuses stops over 6% (`MAX_STOP_PCT`).
  - Weight-target portfolios (rebalance to vol-scaled weights, funding on inventory, hourly stop checks) already exist as a research engine: `research/cross_sectional_momentum/engine.py` (`HourlyData`, `Engine`). It reads Binance archives and would need a Bybit loader for `/data/bt2y`.
- **Positioning features already computed:**
  - Per coin (`PositioningFeedV6`): funding rate, annualised, 90-day percentile and 24h change; OI changes over 1h/4h/24h/3d and 30-day percentile; basis (premium) with 30-day percentile; long-ratio with 24h change.
  - Market-wide (`MarketContextV6`): BTC and ETH trends on 1h/4h/1d, 24h returns, breadth above the daily EMA20, median funding, aggregate OI change over 24h.
- **Data:**
  - Bybit 1m, funding and hourly positioning for 30 coins, 2024-07..2026-09.
  - Alpaca IEX 1m for 10 names, 2023-09..2026-09.
  - **Holdout:** Bybit public REST can backfill 2023-01..2024-09 for older coins (BTC, ETH, SOL, XRP, BNB, DOGE, ADA, LINK, AVAX, LTC, NEAR, UNI, AAVE, ARB, SUI, WLD). The 250-day warm-up means fetching from 2023-01.
  - Hourly OI and premium history depth on Bybit must be verified before any positioning-dependent family claims a 2023 holdout.

## 2. Common validation protocol (applies to every candidate)

| window | dates | use |
|---|---|---|
| DEV | 2024-10-01..2025-10-01 | the only window rules may be adjusted on, with every trial counted |
| TEST | 2025-10-01..2026-10-01 | run once after freezing |
| HOLDOUT | 2023-10-01..2024-10-01 (warm-up from 2023-01) | fetched fresh by `scripts/v5_bybit.py fetch` into a separate directory; **not opened** until DEV+TEST pass and a freeze manifest (code hash + params, as in `docs/V6_FREEZE.json`) is written |

DEV and TEST already overlap the data V6 was chosen on. The HOLDOUT is the only clean window for crypto. The coins that existed then form a smaller universe, which is fine.

**Generic PASS rule** (all must hold):
1. Net return > 0 in **each** of DEV, TEST and HOLDOUT, with fees, spread and recorded funding included.
2. Combined TEST+HOLDOUT daily Sharpe ≥ 0.5. Deflated Sharpe (Bailey & López de Prado 2014) > 0 given the registered number of trials.
3. Still > 0 on TEST+HOLDOUT under **2x costs** (fees + spread) and adverse funding.
4. At least 2/3 of a pre-registered ±25% parameter neighbourhood is positive on TEST. No cell is chosen from that grid.
5. No single coin or month contributes > 40% of net PnL. Result still > 0 without the best 3 days.
6. At least 30 closed trades per window (event families), or at least 40 rebalances (portfolio families).
7. Beats the relevant in-house baseline on return/maxDD: V6.6 for crypto, buy-and-hold SPY for equities.

A PASS goes to a frozen forward paper experiment (as V6), never straight to live.

## 3. Candidates

### C1. V6.6 + V6.2 breadth expansion and pooled book (portfolio construction on existing families)
- **Idea:** keep the frozen V6.2/V6.6 code and parameters exactly. Raise the number of independent bets by running them on more coins (and V6.6 also as SWING), inside one pooled risk book. Return per bot is limited by about 2-3 trades a month; breadth is the cheapest lever.
- **Evidence:** in-house only.
  - V6.6 PF 1.05-1.59 on 4/4 coins; V6.2 positive on 3/4.
  - Grinold & Kahn (2000), *Active Portfolio Management*: information ratio ≈ IC·√breadth. More independent bets at the same edge give more return per unit of risk.
  - V6.6 trades with the market-wide trend, so coins are correlated and the gain is less than √N.
- **Rules:**
  - Universe: the 30 Bybit coins minus the 4 already used, after the frozen V6 tradeability screen (`docs/V6_UNIVERSE.json`). The 4 original coins are reported separately.
  - Signal: 1h (HOURLY) and 4h (SWING), entries and exits unchanged (structural stop 1-6%, 3R target, 24 h / 72 h time stop).
  - Book: one 1,000 USDT pooled account, 1% risk per trade, at most 6 concurrent positions, at most 3 in the same direction from the same BTC regime, total open risk ≤ 5%.
- **Trades/month:** about 2-3 per coin, so about 50-70 across 22-26 coins.
- **Cost drag:** unchanged per trade, about 0.11-0.15% of notional, which is about 0.05-0.1R at 1-3% stops. Funding is small at holds under 72 h.
- **Engine fit:** single-coin `V6Strategy`, already in `scripts/backtest_2y.py`. New code:
  - a job list over more coins (`v6_config.field_plan(coins)`);
  - either sum independent bots, or a pooled book via the V11 `ScanReplayEngine` pattern holding a V6 class per coin (about 150 lines);
  - a `SWING` run for V6.6 (already supported by `V6_CLASSES`).
- **Validation:**
  - The 22+ new coins are an **out-of-sample universe for frozen parameters**: V6.6 was never run on them. Run DEV/TEST on them first, then the HOLDOUT on the 2023-listed subset.
  - Extra pass condition: median coin net > 0 and ≥ 60% of coins positive. Otherwise the 4-coin result was coin luck.
- **Expected if it works:** at 1% risk on a pooled book, about 10-20%/yr, which is **0.03-0.05%/day**. Haircut for selection bias: 0.01-0.04%/day. MaxDD about 10-15%.
- **P(survives):** about 0.40. **Effort:** 1.5/5.

### C2. Daily Donchian-ensemble trend on liquid majors, ATR trailing exit, volatility-scaled risk
- **Evidence:**
  - Zarattini, Pagani & Barbon (2025), *Catching Crypto Trends: A Tactical Approach for Bitcoin and Altcoins* (SFI RP 25-80): an ensemble of Donchian channels with vol sizing on a rotating top-20 coin set reports net Sharpe > 1.5 and about 10.8%/yr alpha vs BTC. This is the authors' own backtest with no independent replication.
  - Moskowitz, Ooi & Pedersen (2012), *Time Series Momentum*, JFE; Hurst, Ooi & Pedersen (2017), *A Century of Evidence on Trend-Following Investing*: Sharpe about 0.7-1.0 across asset classes.
  - Liu & Tsyvinski (2021), *Risks and Returns of Cryptocurrency*, RFS: BTC time-series momentum at 1-8 week horizons.
  - `arXiv:2602.11708` (AdaptiveTrend, cited in-house): about 40% CAGR, 6h trend, long/short.
- **Why it differs from the failed in-house TSM:**
  - Liquid majors only (no top-50 tail).
  - Breakout entry instead of weekly sign rebalancing.
  - A **wide** trailing exit (about 4 ATR from the highest close, or the lower Donchian channel) that lets trades run for weeks; average hold should be 15-40 days, not 4.7.
  - Long-only primary (the published result); shorts only as a pre-registered variant.
- **Rules:**
  - Universe: BTC, ETH, SOL, XRP, BNB, DOGE, ADA, LINK, AVAX, LTC, plus SUI and NEAR where listed. Liquidity screen: 30-day median turnover ≥ 50M USDT.
  - Signal timeframe: 1d (UTC close). Decide at 00:00 + 2 min (lab latency).
  - Entry score = mean over lookbacks L ∈ {20, 40, 60, 90, 150} of 1[close ≥ max(high, L)]. Enter long when the score rises to ≥ 0.4 and BTC's close > its 100-day SMA (market filter, fixed, not tuned).
  - Exit: trailing stop at max(highest close since entry − 4·ATR20, lowest low of the last 20 days). Initial stop = entry − 4·ATR20, typically 10-20%. Stop checked on the 1m tape.
  - Time stop: none. Re-entry on a fresh breakout is allowed the next day.
  - Sizing: 1% of equity at risk on the initial stop (ATR-risk sizing ≈ volatility targeting at about 0.25% daily vol contribution per coin).
  - Portfolio caps: open risk ≤ 8%, gross ≤ 2x equity, at most 8 coins.
- **Trades/month:** about 0.5-1 per coin, so about 5-10 for the book.
- **Cost drag:**
  - Fees and spread about 0.15% round trip on 10-30% average winning moves, so < 1% of gross.
  - **Funding is the main drag:** about 0.03-0.1%/day while long in bull phases, roughly 1-3% of notional per 30-day hold. Model it with recorded settlements.
  - Variant gate: skip entries when the coin's funding is in its 90-day top 5% (pre-registered).
- **Engine fit:** a single-coin event strategy on `ReplayEngine` with TrailSpec. New code:
  - a `DAILY` class (signal 1d, context 1w, `MAX_STOP_PCT` 0.25, no 3R target, no time stop) in a V16 base, either subclassing `V6Strategy` or copying its `on_candle` with those constants;
  - a Donchian-ensemble `setup()`;
  - a chandelier/low-channel trailing exit; check that `TrailSpec("atr", "1d", 4.0)` trails from the highest close, otherwise add a `manage()` exit;
  - harness: `scripts/backtest_2y.py`-style job with a 1,000 USDT book and 260-day warm-up. About 300 lines.
- **Validation:** standard protocol. The holdout year (2023-10..2024-10) contains the 2024 bull leg plus chop, which is a fair test. Baselines: BTC buy-and-hold and equal-weight buy-and-hold of the universe, both net of funding. Add-on pass: return/maxDD ≥ buy-and-hold's.
- **Expected if it works:** book volatility about 20-30%. Net 15-30%/yr, which is **0.04-0.08%/day**. MaxDD 20-30%. Long, flat stretches in chop: expect 6-9 losing months a year.
- **P(survives):** about 0.30. Lowered from the literature by the in-house TSM failure and by publication decay. **Effort:** 3/5.

### C3. Equity trend/momentum sleeve on the Alpaca names (SMA200 + 63-day momentum, monthly)
- **Evidence:**
  - Faber (2007), *A Quantitative Approach to Tactical Asset Allocation*: SMA200 timing, equity-like return with about half the drawdown.
  - Antonacci (2014), *Dual Momentum Investing*.
  - Jegadeesh & Titman (1993) and Asness, Moskowitz & Pedersen (2013), *Value and Momentum Everywhere*.
  - In-house `docs/STOCK_TREND_LIVE.md`, TREND5-SMA200-MOM63, retrospective 2021-2025: +18.95%/yr, Sharpe 0.90, maxDD 17.6%. That used a dated-membership 20-stock universe, not the 10 hand-picked names.
- **Rules:**
  - Primary universe: **SPY and QQQ only**. The 8 single stocks (NVDA, META, AMD, …) were chosen in 2026 with hindsight; including them is survivorship bias. A 10-name variant is reported but not used to pass.
  - Monthly, first session: hold QQQ if its 63-day return > SPY's, otherwise SPY. Hold cash when SPY < its 200-day SMA at the prior close.
  - Execution at the next open, 6 bp cost proxy, no leverage.
- **Trades/month:** about 0.3-0.5.
- **Cost drag:** < 0.1%/yr.
- **Engine fit:** daily bars aggregated from the Alpaca 1m in `/data/bt2y`, using `research/stock_small_account/backtest.py` or `app/live/stock_engine.py` session handling. About 100 lines of study code.
- **Validation:**
  - Data starts 2023-09, so: DEV 2023-10..2024-10 = the HOLDOUT analogue, TEST 2024-10..2025-10, plus 2025-10..2026-10 as a third window.
  - For a fresh, longer holdout, Alpaca daily SIP bars from 2016 cover two real SMA200 exits (2018 and 2022).
  - Pass: net > 0 in each window, and maxDD ≤ 0.7× SPY buy-and-hold maxDD over 2016-2026.
  - **Caveat:** in a 2023-2026 bull market this is mostly beta. Validation cannot separate it from buy-and-hold except through drawdown.
- **Expected if it works:** 8-15%/yr, which is **0.02-0.04%/day**. MaxDD 10-20%. Low correlation with crypto trend (about 0.2-0.4).
- **P(survives):** about 0.45 on absolute terms, low on "alpha". **Effort:** 1.5-2/5.

### C4. BTC/ETH continuous volatility-targeted time-series momentum (needs a ≥ 1,000 USDT book)
- **Evidence:**
  - Moskowitz, Ooi & Pedersen (2012); Moreira & Muir (2017), *Volatility-Managed Portfolios*, JF; Harvey et al. (2018), *The Impact of Volatility Targeting*, JPM.
  - Liu & Tsyvinski (2021): BTC 1-4 week momentum.
  - Typical published BTC trend + vol-target result: Sharpe 1.0-1.3 over 2015-2022, decaying since 2022.
- **Rules:**
  - Daily close. Signal s = mean(sign(r_21d), sign(r_63d), sign(r_126d)), long-only, s ∈ {0, 1/3, 2/3, 1}.
  - Weight = s × min(2, 40% / σ_30d annualised), per coin; book = 50% BTC + 50% ETH.
  - Rebalance daily only when |target − current| > 25% of target (band).
  - Hard stop on each leg at 3 × daily σ × √5 below entry (disaster only).
  - Variant (pre-registered): short-allowed for s = −1.
- **Trades/month:** about 2-4 rebalances per coin.
- **Cost drag:** turnover about 15-25×/yr × 0.07% one-way ≈ 1-2%/yr, plus long funding about 5-10%/yr on average exposure. Funding is the dominant cost and is modelled from recorded settlements.
- **Engine fit:** `ReplayEngine` cannot resize positions. Reuse `research/cross_sectional_momentum/engine.py` (weights, hourly stops, real funding) with a new Bybit loader for `/data/bt2y` (about 200 lines), or a small vectorized daily engine with hourly funding. Must use Bybit fee rates (0.055%), not that engine's 0.04%.
- **Validation:** standard. Baseline: BTC/ETH 50/50 buy-and-hold vol-matched. Pass also requires Sharpe ≥ buy-and-hold Sharpe on TEST+HOLDOUT.
- **Expected if it works:** 10-20%/yr at about 25% vol, which is **0.03-0.05%/day**. MaxDD 15-25%. Highly correlated with C2; run one of the two, or C4 as C2's BTC/ETH core.
- **P(survives):** about 0.30. **Effort:** 2.5/5.

### C5. Positioning regime overlay (aggregate funding, OI and premium) on C1/C2
- **Evidence:**
  - Schmeling, Schrimpf & Todorov (2023), *Crypto Carry*, BIS WP 1087 (CEPR DP 20719, 2025): high futures-spot carry signals trend-chasing leveraged demand and predicts crash and liquidation risk.
  - He, Manela, Ross & von Wachter (2022), *Fundamentals of Perpetual Futures*, arXiv 2212.06888: the funding/basis gap co-moves strongly across coins.
  - In-house counter-evidence: OI/funding added no information at ≤ 4h. At daily/weekly horizons and as a risk scaler (not a signal) it is untested.
- **Rules:**
  - Daily market state from `MarketContextV6` plus two new 7-day aggregates: median funding over the 30 coins (annualised), and aggregate OI change over 7 days.
  - **Crowded** = median funding in its trailing 180-day top 10% **and** aggregate OI 7-day change > +15%.
  - **Flush** = aggregate OI 3-day change < −15%.
  - In Crowded: halve new-entry risk and block new longs on coins whose own `funding_pct_90d` > 0.9.
  - In Flush: allow full risk (post-deleveraging trends are cleaner).
  - All thresholds fixed now; one variant at ±25%.
- **Trades/month:** removes about 10-25% of C1/C2 entries.
- **Cost drag:** none added. It lowers funding paid.
- **Engine fit:** a gate in the `setup()` of a V6 subclass plus 2 new fields in `MarketContextV6.snapshot` (7-day and 180-day windows). About 80 lines. Any V6.2/V6.6 copy with the gate is a **new frozen experiment**; the running V6 stays untouched.
- **Validation:** A/B on the same windows: gated − ungated. Pass if the gated book's return/maxDD beats the ungated one in DEV, TEST **and** HOLDOUT, and the gate is active on ≥ 15% of days. Positioning history for the holdout must exist; if Bybit OI depth is insufficient, the holdout uses funding only.
- **Expected if it works:** +0-3%/yr incremental and 20-30% lower maxDD (+0-0.01%/day). Its value is enabling higher risk at the same drawdown.
- **P(survives):** about 0.25. **Effort:** 1.5/5.

### C6. Funding-crowding cross-sectional long-short (perps only)
- **Evidence:**
  - Koijen, Moskowitz, Pedersen & Vrugt (2018), *Carry*, JFE.
  - Schmeling et al. (2023): high carry precedes crashes.
  - Christin, Routledge, Soska & Zetlin-Jones (2022), *The Crypto Carry Trade*: carry is large but compensates crash risk.
  - Cross-sectional sorts on perp funding/basis have positive long-short returns in a few working papers and theses (e.g. Edinburgh thesis, ERA 1842/43608). No top-journal replication.
  - In-house: the spot-hedged funding sleeve lost on costs; funding had no information at ≤ 4h.
- **What "perps only, no spot hedge" means:**
  - You cannot harvest funding market-neutral per coin. The only neutral form is a **cross-sectional** long-short: short the top-funding coins and long the bottom-funding coins, beta-matched to BTC.
  - Return = funding spread received + price spread. The price leg is a **directional bet that crowded coins underperform**, with explicit squeeze risk: memecoin shorts (FARTCOIN, PUMPFUN, TRUMP, USELESS) can gap +50-100%.
  - It is not an arbitrage. Exclude memecoins from the short leg, or cap their weight at half.
- **Rules:**
  - Weekly, Monday 00:00 UTC. Universe: the 30 coins with ≥ 90 days of history and 30-day turnover ≥ 20M USDT.
  - Rank by 7-day mean funding (8h-normalised). Short the top 5, long the bottom 5.
  - Weights inverse-σ_30d, legs scaled to equal BTC beta, gross 1×, 8% hard per-leg stop.
  - Hold one week, with a 2-rank deadband.
- **Trades/month:** about 15-25 legs.
- **Cost drag:** about 10 legs × 2 sides × 0.07% ≈ 1.4%/month gross notional at full turnover; the deadband roughly halves it. The expected funding spread is about 0.5-3%/month, so costs eat most of the carry in quiet regimes. Only hot regimes pay.
- **Engine fit:** a multi-coin weight book. Reuse the research engine (as in C4) with Bybit data and recorded funding. About 250 lines.
- **Validation:** standard. Report the funding leg and price leg separately. Pass needs both legs ≥ 0 on TEST+HOLDOUT, and no single week > 25% of PnL (squeeze test).
- **Expected if it works:** 0-15%/yr (**0.0-0.04%/day**) with fat left tails.
- **P(survives):** about 0.15. **Effort:** 3.5-4/5.

### C7. Cross-sectional momentum on the 30 Bybit coins (evaluated, lowest prior)
- **Evidence:**
  - Liu, Tsyvinski & Wu (2022), *Common Risk Factors in Cryptocurrency*, JF: 3-week CMOM, strongest in large coins, gross returns, 2014-2018 sample.
  - Fieberg, Liedtke, Poddig, Walker & Zaremba (2025), *A Trend Factor for the Cross-Section of Cryptocurrency Returns*, JFQA: CTREND survives costs in big coins.
  - Dobrynskaya (2023): momentum at 2-4 weeks, reversal beyond.
  - **Against:** the in-house Binance top-50 study failed 0/9 OOS (2022-2024) for both long-only and long-short. The `ML_VS_SIMPLE_STUDY` found intraday cross-sectional ranks reverse, not persist.
- **Only variant worth a test** (differs from the failed one): long-only top-3 of the 30 by 21-day return (the CMOM horizon, not 63), conditional on BTC > 100-day SMA, weekly, equal-risk, no tight stops (8% disaster stop), hedged with a BTC short of equal beta as a pre-registered variant.
- **Trades/month:** about 6-10 legs.
- **Cost drag:** about 0.5-1%/month at full turnover.
- **Engine fit:** research weight engine, as in C4/C6.
- **Validation:** standard, with an extra baseline of equal-weight 30-coin buy-and-hold under the same BTC filter.
- **Expected if it works:** 0-20%/yr with high variance.
- **P(survives):** about 0.10. **Effort:** 3/5.

### Rejected: daily cross-sectional reversal with maker entries
- **Evidence:** Bianchi, Babiak & Dickerson (2022), JBF: reversal returns are concentrated in illiquid pairs.
- **In-house:** the reversal effect is real, but the best maker variant was +1 bp per trade. V13 Snapback (maker reversion, 29 alts) failed 54/54 variants. Daily turnover of the whole book with 4-11 bp of costs leaves almost nothing on 30 liquid perps.
- **Expected:** −0.02 to +0.01%/day. Not worth building.

## 4. Ranking

Score = expected validated return (%/yr, at the stated risk) × P(survives the full protocol) ÷ effort (1-5).

| rank | candidate | E[ret]/yr if it works | P(survive) | effort | score | daily if it works |
|---:|---|---:|---:|---:|---:|---|
| 1 | **C1** V6.2/V6.6 breadth expansion + pooled book (+SWING) | 12 % | 0.40 | 1.5 | **3.2** | 0.01-0.05 % |
| 2 | **C3** SPY/QQQ SMA200 + 63d momentum sleeve | 10 % | 0.45 | 2.0 | **2.3** | 0.02-0.04 % |
| 3 | **C2** daily Donchian-ensemble trend on majors, 4-ATR trail | 20 % | 0.30 | 3.0 | **2.0** | 0.04-0.08 % |
| 4 | **C4** BTC/ETH vol-targeted TSM (≥ 1,000 USDT book) | 14 % | 0.30 | 2.5 | **1.7** | 0.03-0.05 % |
| 5 | **C5** positioning regime overlay on C1/C2 | +3 % (and −25% DD) | 0.25 | 1.5 | **0.5** | +0-0.01 % |
| 6 | **C6** funding-crowding XS long-short | 8 % | 0.15 | 3.75 | **0.3** | 0.0-0.04 % |
| 7 | **C7** XS momentum (21-day, top-3 long) | 10 % | 0.10 | 3.0 | **0.3** | 0.0-0.05 % |
| — | daily XS reversal (maker) | ~0 | 0.05 | 4 | rejected | ≈ 0 |

C2 has the highest upside. C1 has the best ratio of upside to cost and risk, because it only re-tests frozen code on unseen coins and the holdout year. C3 is the best diversifier, but its return is mostly equity beta. C4 overlaps C2; build C4 only if a ≥ 1,000 USDT BTC/ETH book is acceptable.

## 5. Portfolio construction if more than one passes

- **Sleeves:** C1 (hourly V6 pooled), C2 (or C4) daily trend, C3 equity sleeve. C5 is an overlay, not a sleeve.
- **Risk budget:** equal **risk** (not capital) per sleeve, using inverse 60-day realised vol of each sleeve's daily PnL, rebalanced monthly. Total target 15-20% annual vol. A hard portfolio stop at 25% below peak halts every sleeve (as V6's 30% halt).
- **Expected correlations:** C1-C2 about 0.4-0.6 (both trend, different horizons); crypto-C3 about 0.2-0.4.
- **Combined Sharpe:** about 1.2-1.4× the best sole sleeve. At 15-20% vol and a combined Sharpe of 0.8-1.2, that is about 12-24%/yr, or **0.03-0.065%/day**.
- **This is the realistic ceiling for a validated lab book:** roughly 2-3× the current V6 per-bot rate. Higher daily numbers come only from leverage, which scales drawdown one-for-one.
- **Validate the combination itself:** freeze sleeve weights from DEV vol only, then run TEST+HOLDOUT as one book. Pass only if the combined return/maxDD beats the best single sleeve's.

## 6. Recommended order of work

1. **C1:** jobs already exist. Run frozen V6.2/V6.6 on the 22+ unused coins (DEV/TEST), then fetch the 2023-10..2024-10 holdout for the older coins and run once.
2. **C3:** fetch Alpaca daily SIP from 2016 and run the SPY/QQQ study, about one day of work.
3. **C2:** add the DAILY base class and the Donchian ensemble. Pre-register params now (the values above), run DEV, freeze, then TEST, then HOLDOUT.
4. **C5** as an A/B on whatever passed. Then C4/C6/C7 only if a weight-book engine on Bybit data is built anyway.

Every step writes a pre-registration JSON (rules, pass rule, trial count) before the first run, as in `docs/V5_TEST_PREREGISTRATION.json`.

## Sources
- [Zarattini, Pagani, Barbon — Catching Crypto Trends (SFI RP)](https://ideas.repec.org/p/chf/rpseri/rp2580.html), [author page](https://abarbon.com/papers/catching-crypto-trends)
- [Schmeling, Schrimpf, Todorov — Crypto carry, BIS WP 1087](https://www.bis.org/publ/work1087.pdf)
- [He, Manela, Ross, von Wachter — Fundamentals of Perpetual Futures](https://arxiv.org/pdf/2212.06888v5)
- [Liu, Tsyvinski, Wu — Common Risk Factors in Cryptocurrency (NBER w25882)](https://www.nber.org/papers/w25882.pdf)
- [Fieberg et al. — A Trend Factor for the Cross Section of Cryptocurrency Returns, JFQA](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/trend-factor-for-the-cross-section-of-cryptocurrency-returns/4C1509ACBA33D5DCAF0AC24379148178)
- [Bianchi, Babiak, Dickerson — Trading volume and liquidity provision in cryptocurrency markets (Riksbank WP 413)](https://www.riksbank.se/globalassets/media/rapporter/working-papers/2022/no.-413-trading-volume-and-liquidity-provision-in-cryptocurreny-markets.pdf)
- [Edinburgh thesis on perp cost-of-carry cross-section](https://era.ed.ac.uk/handle/1842/43608)
- Standard references cited from memory, not re-fetched: Moskowitz-Ooi-Pedersen 2012 JFE; Hurst-Ooi-Pedersen 2017; Liu-Tsyvinski 2021 RFS; Moreira-Muir 2017 JF; Harvey et al. 2018 JPM; Koijen-Moskowitz-Pedersen-Vrugt 2018 JFE; Faber 2007; Antonacci 2014; Grinold-Kahn 2000; Bailey-López de Prado 2014.
