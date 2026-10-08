# PaperLab research-history audit (read-only), 2026-10-08

Scope: every study artefact under `paperlab/docs/` (all `*_STUDY*.json`, `*_PROTOCOL.md`, `*RESULTS_FREEZE*.md`,
`V4_ROOT_CAUSE.md`, `SYSTEM_REVIEW.md`, `BACKTEST_2Y_SUMMARY.json`, `V15_*`), `README.md` "Results so far",
`research/` outputs (cross-sectional momentum, regime-filtered crypto, stock small account, video breakout,
DuckDB studies), `app/core/programs.py`. Paths below are relative to `D:\Projects\BinanceBOt\paperlab\`.

Conventions:
- R = the trade's planned risk. "Gross" = before fees/spread; in the 2-year backtest gross ≈ net + fees (funding
  excluded, it is small).
- "Both halves" = the study's own pre-registered split.
- Inferences are marked **[INFERENCE]**.
- 2-year backtest books are 20 USDT crypto (1% risk per trade, live sizing) and 1000 USD stocks. A live rule halts a bot
  at 30% below peak. Halted bots are also reported "without_stop": none recovered.

---

## 1. Every strategy family / study tested

### 1a. Two-year backtest of all 93 live bots (2024-10-01 .. 2026-10-01, real fees, funding, live code)

Source: `docs/BACKTEST_2Y_SUMMARY.json`. Caveat recorded in the file: "several strategies' settings were chosen on parts
of this period: not an unseen test". Family aggregates were computed from that file.

| Family | Market / TF | Hold | Bots | Trades | Net return (sum of books) | Gross (net+fees) | Fees as % of start equity | Bots positive | Max DD | Both years? | Stated failure reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **V6.6** market-aligned alt trend | Bybit perps ARB/ENA/XRP/DOGE, 1h (4h+1D context) | ≤24 h | 4 | 270 | **+15.0%** | +14.25 USDT | 2.8% | 4/4 | 7.2–11.3% | XRP, ARB, DOGE: yes. ENA: y1 −1.04, y2 +3.74 | — (survivor) |
| **V6.2** momentum + OI | same, 1h | ≤24 h | 4 | 195 | **+4.9%** | +5.8 | 2.4% | 3/4 | 7.7–11.2% | ARB: yes. DOGE, ENA: y2 negative. XRP: −5.2% | — (partial survivor) |
| V6.1 positioning pullback | same, 1h + 4h | 24–72 h | 8 | 455 | −1.8% | +1.86 | 2.9% | 4/8 | 1.1–27% | mixed | no consistent edge (XRP-4H +14.4% on 17 trades, PF 3.36) |
| V6.3 funding-crowding reversal | same, 1h | ≤24 h | 4 | 332 | −20.7% | −13.6 | 3.7% | 0/4 | up to 30% (XRP halted) | no | negative gross |
| V6.4 OI breakout | same, 1h | ≤24 h | 4 | 128 | −7.7% | −4.7 | 1.9% | 0/4 | 11–18% | no | negative gross |
| V6.5 deleveraging reversal | same, 1h | ≤24 h | 4 | 12 | −0.5% | −0.3 | 0.2% | 1/4 | ≤3% | — | too few trades |
| V7.1 / V7.2 15m trend + positioning | same, 15m | hours | 8 | 3,625 | −23.5% / −20.3% | **+8.0 / +10.4** | 33% | 0/8 | ~30%, all halted | no | **gross-positive, fee-destroyed** |
| V8.3 VWAP snap-back (+LADDER twins) | 6 Bybit perps, 5m | 45 min → 3 h | 12 | 3,621 | −28.8% | −10.5 | 24.5% | 0/12 | 30%, all halted (no-stop: −40 to −85%) | no | no gross edge plus fees |
| V9.1–V9.3 stock scalpers | 10 US stocks/ETFs, 5m, Alpaca IEX | ≤45 min | 30 | 19,577 | −21% to −27% | −1,550 to −2,212 USD per family | 4–6% | 0/30 | 21–31%, most halted | no | no entry edge; 0.25% stop inside 5m noise |
| V11.1–V11.4 multi-coin scanners | 30 Bybit perps, 5m, 25/50/25 TP ladder | minutes–hours | 4 | 2,540 | −25% to −32% | −3.2 to +3.3 | 15–46% | 0/4 | 30%, all halted | no | no edge after fees |
| V12.1 Bizzy day breakout (Larry Williams k=0.5) | ETH/SOL/HYPE, daily | intraday, 2x notional | 1 | 376 | **−63.5%** | −4.7 | 40% | 0 | 88.6% | no | negative gross, 2x size |
| V13.1 maker snap-back ("Bounce") | 29 alts, 5m limit entries | ≤4 h | 1 | 690 | −16.7% | +2.0 | 27% | 0 | 32% | no | thin gross, fees |
| V14.1–V14.4 HTF-filtered copies | V8/V11/V13 universes | as parents | 9 | 3,953 | −22.8% to −29% | −13.7 to +4.4 | 18–48% | 0/9 | 30%, all halted | no | the HTF filter did not create an edge |

### 1b. Pre-registered studies and arenas (chronological)

| Study (file) | Market / TF / hold | Sample | Result after costs | PF | Halves | Stated reason |
|---|---|---|---|---|---|---|
| V1 multi-year (`docs/V4_ROOT_CAUSE.md` §2) | Binance BTC/ETH/SOL, 1m–15m intraday | 69 bots, 78,199 trades | gross −5,047; fees 3,301; slippage 1,643; **net −9,992 USDT** | — | — | NO RAW EDGE |
| V2 dev / test / multi-year (`V4_ROOT_CAUSE.md`) | Binance perps, intraday | 104 / 26 / 3 bots | net −265 / −17 / −13 | — | — | NO RAW EDGE. S26-XRP gross +2.5 bp vs 14.3 bp cost (`V3_HYPOTHESES.md`) |
| V3 aggressive discovery (`V3_RESULTS_FREEZE.md`, `V4_ROOT_CAUSE.md`) | Bybit, 1m–30m, 6 families | 300 bots, 31,990 trades | gross −320, fees 650, **net −1,119**. −5.4 bp gross, −18.9 bp net of turnover | — | — | 27/30 family×TF cells gross-negative. Fades worst (S36 −35 to −48 bp) |
| V3.1 edge-gated (`V31_RESULTS_FREEZE.md`) | Bybit 5m–30m | DEV + TEST | ADVANCED SET = NONE | — | — | only S33.1 pullback 15m positive in both windows: +2.38 USDT / 181 trades, +3.70 / 149 |
| V4 intraday specialists (`V4_RESULTS_FREEZE.md`) | Bybit 3m–30m, 7 families | DEV + holdout 2025-04..09 | raw gross R negative on every TF: DEV −0.037 to −0.085 R, holdout −0.071 to −0.100 R | — | no | NO RAW EDGE |
| V5 hourly/swing positioning (`V5_RESULTS_FREEZE.md`) | Bybit 1h / 1D-1W, 8 families | DEV 2,982 trades; TEST 2,437 | DEV net −41.6 USDT (gross +1.58, taker fees 27.2, slippage 17.0); TEST net −13.8 (gross +20.1) | — | V5.4 SWING replicated on TEST only (+0.223 R gross, n=196, P=0.005) | costs > gross. 39% of setups illegal at 20 USDT (min notional) |
| V8 exit / hold / maker / cost studies (`V8_*_STUDY.json`) | 6 perps, 5m, 45 min hold, 60 days | ~13,000 trades per variant | BASE −0.284 R/trade, PF 0.52. Gross −0.039 R; fees ≈ 0.20–0.24 R | 0.36–0.68 | none profitable | ladders, BE stops, holds and maker entries only shrink the loss. Maker LMT_LTP −0.185 R |
| V8 trend fix + independent confirm (`V8_TREND_FIX_*.json`) | 2025-01..2026-06 independent window | 13,657 / 12,726 trades | V8.1 −0.079 R, V8.2 −0.073 R. LONGONLY and HTF worse | 0.87 | no | NOT ADOPTED |
| V8.3 snap hold (`V8_SNAP_HOLD_STUDY.json`) | 94 days | 7,341 BASE trades | −0.10 R at every hold (45 m … day close) | 0.58–0.79 | — | H180 adopted (least bad) |
| V9 stock single changes (`V9_STOCK_STUDY.json`) | 10 stocks, 5m, ≤45 min, 1000 USD daily reset | 42,791 BASE trades | BASE −21,259 USD. Best combos still −0.029 R/trade | 0.82–0.92 | every combo negative in both years | stops in 5m noise (stopped <10 min: −0.66 to −0.79 R); commission only ~18% of the loss |
| V9 signal grid (`V9_SIGNAL_STUDY.json`) | 104,146 setups × 480 exits × 12 filters, chosen on year 1 | — | year 2 still negative for all three families (e.g. V9.2 −673 USD, −0.021 R) | 0.82–0.85 | no | NOT FIXED. Target 0.75 R → 3 R changes nothing; no filter helped |
| V11 scan study (`V11_SCAN_STUDY.json`) | 30 perps, 5m, 45 min | 51,866 candidates | gross −0.02 to +0.02 R, net −0.21 to −0.29 R | 0.46–0.61 | no | no scanner edge |
| V11 cost / zap / TP1 / ladder / hold / spacing / breakout-stop studies | 30 perps | 600–9,000 trades per variant | every variant negative. Best V11.1 MAKER_TP+MINSTOP12 −0.058 R | 0.44–0.93 | some "improves", none positive | ladders raise win rate to 55–66%, PF stays < 1 |
| V11 reversal (`V11_REVERSAL_STUDY.json`) | 1h shock ≥4 ATR, 30-min hold | 235 / 586 events | DEV −2.9 bp net, CONF +12.0 bp (t 0.79) | — | not confirmed | — |
| V11 crash study (`V11_CRASH_STUDY.json`, 2 years) | V11.3 flush-reversal, 30 coins | 41,011 trades BASE | BASE −513 USDT (−0.071 R; fees 370 ≈ 72% of the loss). COMBO (maker + confirm) −161 (−0.046 R) | 0.73–0.82 | negative both years | maker entries and confirmation shrink the loss only |
| V12 Bizzy (`V12_BIZZY_STUDY.json`) | ETH/SOL/HYPE/BTC daily breakout, 2x | 589 trades over 900 days (1h) | **−98.8%** (all 4), BTC alone −39% | — | every year negative except BTC 2024 | negative gross |
| V13 Snapback (`V13_SNAPBACK_STUDY.json`) | 29 alts, maker limit reversion, ≤4 h | 54-variant grid; selected 320 DEV / 421 TEST trades | DEV +0.022 R → TEST **−0.089 R** | 0.81 | **FAIL** | DEV selection noise. The taker version is also negative |
| V14 HTF (`V14_HTF_STUDY.json`, revision, follow-up) | V8/V11/V13 with a 4h/1D trend filter | 300–3,400 trades | HTF helps 1/4 (V14.4) in study 1, 0/4 in revision/follow-up. All remain negative | 0.72–0.88 | — | HTF filter is not an edge |
| V15 stock day traders (`V15_DAYTRADE_STUDY.json`) | 10 stocks, 1m, flat by close | 501 sessions | V15.1 +1,013 USD (4,183 trades, +0.050 R); V15.2 +612 (596 trades); V15.3/4/5 negative | V15.1 1.058; V15.2 1.215 | V15.1 both years positive (+569/+444), V15.2 y1 R negative | all five FAIL (PF < 1.10 or < 6/10 symbols) |
| V15.1 holdout (`V15_HOLDOUT.json`) | 2023-10..2024-10, 5 pre-chosen symbols | 1,090 trades | **−355 USD, −0.044 R** | 0.93 | — | FAIL, 1/5 symbols positive: the both-years pattern was selection |
| Alt-data (`ALTDATA_STUDY.json`) | 30 perps, 99 days, 853,530 decisions, 22 features, 5m–4h | — | nothing TRADEABLE after taker costs. Only MAKER-ONLY: XS 4h `ma_slope` (+1.1/+0.6 bp) | — | — | the only robust effect is short-term reversal, 0–5 bp gross vs ~11 bp taker round trip. OI, funding, L/S ratio and taker imbalance were REMOVED |
| ML vs simple (`ML_VS_SIMPLE_STUDY.json`) | 30-coin ranking, 15m/1h, walk-forward | 10 folds | IC 0.034–0.038 (ML) vs 0.029–0.032 (simple). Spread gross +0.1 to +0.9 bp, net −10 to −11.7 bp | — | — | ML NOT JUSTIFIED |
| Squeeze (`SQUEEZE_STUDY.json`) | neg funding + OI jump + drop, 1h/4h | 88 events / 53 days | raw +9.9 bp (t 0.54); market-adjusted −1.6 bp; without best 3 days −14.9 bp | — | — | not a candidate |
| News / attention (`V10_NEWS_STUDY.json`, `NEWS_EVENT_STUDY.json`) | 15,304 news items, 180 days | 4,816 crypto events | crypto −15.1 bp net (t −13.3); stocks −2.9 bp | — | — | attention does not predict volatility; tone does not predict direction |
| Cross-sectional momentum (`research/cross_sectional_momentum/output/summary.md`) | top-50 perps, 63-day lookback, weekly, OOS 2022–2024 | 9-cell grid | long-only −0.45%/month, L/S −0.56%/month. TS momentum −4.54%/month (DD 91%) | — | IS also negative | 0/9 cells positive |
| Regime-filtered momentum + funding carry (`research/regime_filtered_crypto/output/run_7a36d262871367ef/REPORT.md`) | top-50 perps + spot hedge, daily, OOS 2022–2025 | — | combined −0.45%/month (−20%, killed 2023-06). **Funding-only GROSS +23.3% (DD 2.1%, Sharpe 7.6), NET −20%** | — | IS positive, OOS negative | fees 14.4k + slippage 7.2k vs carry 4.0k: churn ate the carry |
| Autonomous research (`docs/AUTONOMOUS_RESEARCH.md`, `RAILWAY_RESEARCH_LIVE.json`) | Binance spot/perps, 1h templates | 35 markets studied in the snapshot | almost all REJECTED. 1 paper probation (AAVE breakout 20/2.5 ATR: holdout 11 trades, PF 1.46) | — | — | selection bias acknowledged. No forward evidence yet |
| Video BTC 4h breakout (`research/video_breakout/output/29a1fe615628aa50/`) | BTCUSDT spot, 4h, 7-day high entry / 3-day low exit, long/cash | 61 trades, 2022–2025 | **+174.9%**, max DD 32.3%, PF 1.83 (my calculation from trades.csv) | 1.83 | by year: 2022 −165 USDT, 2023 +738, 2024 +1,013, 2025 +163 | rules frozen before evaluation (from the video). Not a pre-registered holdout of ours; 2022 lost |
| Stock trend 5 (`research/stock_small_account/REPORT.md`) | 5 of the liquid-20 S&P names, 63-day momentum, SMA200 gate, monthly | OOS 2021–2025 | **+18.95%/yr**, max DD 17.6%, Sharpe 0.90. IS 2001–2020 +4.75%/yr, DD 34% | — | 5/5 cost seeds positive | "RETROSPECTIVE ONLY", not fresh OOS |
| Live forward paper (`PAPER_TRADE_AUDIT.json`, 3,156 trades) | V6/V7/V8/V11/V12 | — | every CONTROL family negative. V8 fees 11–19 USDT vs gross −2.6 to −4.3 | — | — | early V6.2/V6.6 forward: 2 and 6 trades, both negative (too small to judge) |

---

## 2. Families that showed any genuine edge

1. **V6.6 MARKET_ALIGNED_ALT_TREND (1h).** It is long or short an alt only when BTC 4h and 1D agree, ETH 1D is not
   against, breadth is ≥ 60%, aggregate OI is not unwinding, the alt's 4h trend agrees, and the alt outperforms BTC
   over 24 h and closes at a new 24-bar extreme (`docs/V6_PROTOCOL.md` §3). The 3R target, structural stop, 1–6% clamp
   and 24 h time stop make it a regime-gated trend follower.
   - 2-year results: XRP +24.46% (81 trades, PF 1.52, DD 7.3%), ARB +19.8% (67, PF 1.53, DD 7.2%), ENA +13.5% (43, PF 1.59, DD 10.6%), DOGE +2.1% (79, PF 1.05).
   - All 4 are positive in total. 3/4 are positive in both years; ENA lost in year 1.
   - Family: +15.0% on 80 USDT, 270 trades.
   - Fees were only 2.8% of starting equity, against 24–48% for the fast bots.
2. **V6.2 MOMENTUM_OI (1h).** A top-15% multi-hour impulse with OI up ≥ 2%, a 2–6 bar consolidation, then the break.
   - 2-year results: ARB +18.8% (48 trades, PF 1.71, DD 7.7%, both years positive), DOGE +3.65%, ENA +2.33%, XRP −5.2%.
   - Year 2 was negative on DOGE, ENA and XRP. The edge is weaker and less consistent than V6.6.
3. **V5.4 TREND_PULLBACK_POSITIONING SWING (1D/1W, 12–72 h holds).**
   - Replicated gross on the V5 pseudo-holdout: +0.223 R, n = 196, P(mean ≤ 0) = 0.005, both sides positive. DEV was +0.073 R (P = 0.20).
   - At 20 USDT, 60% of its setups were below the exchange minimum, so its book netted only +0.019 R (`docs/V5_RESULTS_FREEZE.md`).
   - V6.1 is its descendant and was flat over 2 years (−1.8%).
4. **Slow trend / momentum outside the arena** (retrospective, different markets):
   - BTC 4h Donchian-style breakout, long/cash: +174.9% over 2022–2025, 61 trades, PF 1.83, DD 32%. Buy-and-hold over the same 2022–2025 window: +89.4%, DD 67% (`regime_filtered_crypto` report).
   - 5-stock 63-day momentum with an SMA200 gate: +18.95%/yr OOS 2021–2025 (DD 17.6%).
   - Mechanism: time-series trend persistence at multi-day horizons, low turnover.
5. **Funding carry (delta-neutral) gross.** OOS 2022–2025 gross +23.3%, DD 2.1%, Sharpe 7.6, but net −20% after
   fees and slippage on churned hedge legs. The report's break-even: a threshold-level carry of 0.015%/day needs about
   16 days of holding to recover the 0.24% round-trip cost.
   **[INFERENCE]** A real but small gross edge. Viable only with very low turnover and maker execution; at best about
   5–6%/yr.
6. **Short-term cross-sectional reversal.**
   - Statistically robust: |t| 5–13 at every horizon, both halves.
   - Worth 0–5 bp gross against an ~11 bp taker round trip. Maker-only bound: +1.1 / +0.6 bp (4h MA-slope XS reversal) (`docs/SYSTEM_REVIEW.md` §0).
   - Not tradeable for profit as tested. V13 (the maker version) failed on TEST.
7. **Near-misses that did not hold:**
   - S33.1 15m pullback: +2.38 / +3.70 USDT, failed the concentration gate.
   - V15.1 opening-candle trend: PF 1.06 over 2 years, then −355 USD on the holdout.
   - V15.2 stocks-in-play: PF 1.215, but year-1 R was negative.
   - V11 1h ≥ 4-ATR shock reversal: CONF +12 bp, t 0.79.

---

## 3. Cross-cutting lessons

**Turnover / holding period vs cost drag** (2-year backtest fee totals as a share of starting equity, from
`BACKTEST_2Y_SUMMARY.json`):

| Holding period | Fees as % of starting equity |
|---|---|
| 1h–24h holds (V6) | 0.2–3.7% |
| 15m (V7) | 33% |
| 5m crypto (V8.3, V11, V13, V14) | 15–48% |

- V7 was **gross-positive** (+8.0 / +10.4 USDT) but fees of 26.6–26.8 USDT per family turned it into −20% to −24%.
- V3 fees were 2× its gross loss. The V8 fee load is ~0.20–0.24 R per trade against gross −0.04 R.
- V11 crash study: fees ≈ 72% of the loss.
- Gross edge per trade at intraday timeframes was −7 to +5 bp (V3: −4.6 to −7.3 bp at every TF). The taker round trip
  is 11–15 bp. 1m/3m/5m trade 3–4× more often, so they lose faster (`V4_ROOT_CAUSE.md` §2).
- **Hopeless (repeatedly measured):**
  - every crypto entry at 1m–30m (V1–V4, V7, V8, V11, V13, V14: thousands of trades, gross ≈ 0 or negative);
  - US-stock 5m scalps (V9). Costs are small there (~18% of the loss), but the stops sit inside the noise;
  - stock opening-range / day-trade families (V15);
  - daily breakout with 2x size (V12);
  - funding-crowding reversal (V6.3, V5.2) and OI breakouts (V6.4, V5.3).
- Maker execution consistently **reduces** losses by 15–35% but never flipped a family positive:
  - V3.1: about −15% of the loss;
  - V8: −0.249 → −0.185 R;
  - V11 crash: −0.071 → −0.049 R;
  - V13: DEV positive, then TEST −0.089 R.
  - Paper maker fills are optimistic (no queue, no adverse selection; `SYSTEM_REVIEW.md` §6).
- **Signal types:**
  - **Trend / regime alignment at 1h+ with low turnover:** the only family to make money (V6.6, V6.2-ARB). It also
    worked outside the arena on multi-day TFs (BTC 4h breakout, stock 63-day momentum).
  - **Cross-sectional momentum** on perps (weekly, 63-day): failed, 0/9 cells.
  - **TS momentum (weekly, 2–5% caps):** −4.5%/month.
  - **Positioning data (funding / OI / premium / L/S):** no information beyond price at 5m–4h (`ALTDATA_STUDY.json`).
    The premium is informative at 5–15 min but < 1 bp.
  - Positioning used as a **context gate** inside a trend rule (V6.6's aggregate OI / breadth) coexists with the one
    winner. **[INFERENCE]** Its marginal value is unproven; the trend/regime conditions may be doing the work.
  - **Mean reversion / fades:** worst everywhere (V3 S36 −35 to −48 bp; V6.3 −20.7%; V13 FAIL; V8.3 −28.8%). The
    reversal effect is real but below costs.
  - **Breakouts:** intraday breakouts lost (V3, V4.7, V11.1, V15.3, V12). The multi-day BTC 4h breakout worked
    retrospectively.
  - **Take-profit ladders / break-even locks:** raise the win rate (to 55–66%) but never PF. V8 exit study: every
    ladder −0.284 to −0.290 R, worse than or equal to BASE. Exits cannot create an entry edge (`V4_ROOT_CAUSE.md` §3:
    20 counterfactual exits, all negative).
  - **Wider minimum stops:** consistently improve net R by cutting fees per R (V8 MINSTOP80 −0.149 vs −0.283 R; V9
    STOP50/1%). They never make a strategy positive.
  - **Higher-timeframe filters (V14):** helped 1 of 4 in the first study, 0 of 4 after revision and follow-up. V8 HTF
    was worse on the independent window.
  - **ML (ridge / GBM):** IC slightly above simple, spreads < 1 bp. Not justified.
  - **News / social:** no effect.
- **Jev AI twins vs controls:** discrimination was never distinguishable from chance.
  - AUC 0.512 (V1), 0.511 [0.496, 0.527] (V2), 0.500 (V3.1 DEV), 0.546 [0.499, 0.593] (V3.1 TEST), 0.477 (V4 DEV),
    0.542 (V4 TEST).
  - Selection alpha vs a matched random action is negative: −2.52, −0.32, −1.16, −0.85 USDT. ATTACK sizing added
    losses (−1.63, −0.47) (`V3/V31/V4_RESULTS_FREEZE.md`, `V4_ROOT_CAUSE.md` §4).
  - In live paper, Jev twins lost less mainly by trading less (V8.1 JEV 114 trades / −3.50 vs CONTROL 347 / −14.80;
    V11 JEV books +0.07 to +0.17 on 7–25 trades) (`PAPER_TRADE_AUDIT.json`). Memory note: "beat losing controls by
    not trading".
- **Small-account constraint:** at 20 USDT, 39–65% of setups were illegal at the exchange minimum (V5, S26). This
  biases the traded sample and blocked V5.4 SWING's economics.
- **Selection traps:** coin choice by PnL (S31-ZEC lost on all 9 other coins), best-of-grid (V13), and keeping only
  "positive in both years" symbols (V15.1 holdout 1/5) all failed out of sample.

---

## 4. Best achievable average daily return observed

Compounded: (1+R)^(1/days) − 1. The crypto books are 1% risk per trade.

| Item | Return | Per day | Validation status |
|---|---|---|---|
| Best single bot: V6.6-XRP-1H | +24.46% / 730 d | **0.030%/day** | 2-year backtest of frozen code, partially overlapping design data |
| V6.6-ARB | +19.8% | 0.025%/day | same |
| V6.2-ARB | +18.8% | 0.024%/day | same |
| V6.6 family (4 bots, equal weight) | +15.0% | 0.019%/day | same |
| The 7 GO-LIVE V6.2/V6.6 bots, equal weight | +12.1% / 2 years (404 trades; y1 +11.43, y2 +5.50 USDT on 140) | **0.016%/day** (≈ 5.9%/yr) | same |
| BTC 4h breakout, 1x spot | +174.9% / 4 years | **0.069%/day** (≈ 29%/yr, DD 32%) | retrospective; lost in 2022 |
| 5-stock trend | +18.95%/yr | 0.048%/calendar day | retrospective |
| V15.1 (failed holdout) | +1,013 USD / 10 bots / 501 sessions on 1000 USD | 0.020%/session | holdout negative |
| Funding carry | gross 0.014%/day | net negative | — |

**[INFERENCE]** Best combination:
- V6.6 risk raised from 1% to ~3% per trade on its 3 best coins → ~0.06–0.09%/day (≈ 22–36%/yr). The DD of 7–11%
  scales to ~21–33%, and min-notional constraints loosen.
- Plus a BTC/ETH 4h trend sleeve.
- A realistic validated ceiling is **≈ 0.05–0.10%/day (≈ 20–40%/yr) with 20–35% drawdowns**.
- No study supports anything near 1%/day. Every bot that traded often enough to target high daily returns lost 15–90%.

---

## 5. Recommendations

**Worth new research (in order of evidence):**
1. **Regime-gated, low-turnover trend following at 1h–1D on liquid perps / majors.** V6.6 is the template.
   - Test on data V6 never saw (pre-2024-10 Bybit history, e.g. 2021–2024, and more coins chosen by a rule, not by PnL).
   - Use pre-registered gates: both halves, PF ≥ 1.2, net > 0 without the best 3 trades.
   - Then study position sizing (risk 1% → 2–3%) and multi-coin portfolio aggregation as the return lever. Signal
     frequency is not the lever.
2. **Multi-day breakout / trend on BTC/ETH (4h–1D)**, long/cash or long/short with a vol-target.
   - Validate the video rule family on 2018–2021 and ETH as a pre-registered holdout before trusting the +175%.
3. **Portfolio of independent slow edges:**
   - V6.6 crypto trend + stock monthly momentum + low-turnover funding carry (hold ≥ 16 days, maker legs).
   - Diversification is the only evidence-consistent way to raise return per unit of DD.
4. **Size and account constraints:** run slow strategies on books large enough (≥ 100–200 USDT) that exchange minimums
   do not veto 40–65% of the setups.
5. **Order-book / liquidation data** (SYSTEM_REVIEW R8) is the only untested input for fast trading. Collect it forward
   before any new scalping idea.

**Dead ends to avoid (each failed with thousands of trades and pre-registered tests):**
- Any taker strategy holding < 4 h on crypto (1m–30m), including scanners, VWAP snap-backs, intraday breakouts and pullbacks.
- 5m stock scalps and stock day-trading families (V9, V15).
- Daily-range breakouts with leverage (V12).
- Funding / OI / premium / L/S as direct signals, and the funding-squeeze bounce.
- Mean-reversion fades (taker or maker).
- Cross-sectional perp momentum (weekly, 63-day).
- News / sentiment / attention signals.
- ML ranking on the same features.
- Jev / LLM trade gating (AUC ≈ 0.5).
- TP ladders, break-even locks and HTF filters as "fixes".
- Re-tuning exits on entries without gross edge.
- Coin or symbol selection by past PnL.
