# Renata V12: strategy-family research (round 1)

**Verdict: no strategy clears all three pre-registered stages, so no Renata V12-RESEARCH bot is built.**
Renata V11.4 stays unchanged. No live setting, API permission, execution path or existing bot config was touched.

- **Data:** `docs/V12_RESEARCH.json` (machine-readable, every config on every window).
- **Code:**
  - `app/lab12/engine.py`, `app/lab12/families.py`, `scripts/v12_research.py`;
  - tests in `tests/test_lab12.py`;
  - pre-registered in commit `ac56bd9` before any run.
- **Run:** 2026-10-10 on the Railway volume (`/data/v12`). Each window takes 40–120 s.

## 1. Data audit and feasibility

| Window | Archive | Period | Coins | Extra data | Use |
|---|---|---|---|---|---|
| DEV1 | `/data/bt_hold2` (Bybit) | 2021-12-01 .. 2023-07-01 | 18 | funding | stage 1 |
| DEV2 | `/data/bt_hold_crypto` (Bybit) | 2023-08-05 .. 2024-07-01 | 22 | funding, OI, ratios, premium (1h) | stage 1 |
| VAL | `/data/bt2y` (Bybit) | 2024-08-05 .. 2026-09-30 | 30 | funding, OI, ratios | stage 2 |
| FINAL | `/data/bt_final` (Binance public archive, fetched for this study, never used before) | 2020-01-01 .. 2021-10-01 | 16 | funding | stage 3 |

Feasibility per family:

| Family | Status |
|---|---|
| A momentum | feasible |
| B breakouts | feasible |
| C regime overlay | feasible |
| D BTC→alt lead-lag | feasible |
| E volatility | feasible |
| G1 funding extremes | feasible on all windows |
| G2 open interest | feasible only from 2023-07 (DEV2 + VAL), so it has no final stage |
| F microstructure | **not feasible**: no historical order-book or trade data exists, and nothing was simulated in its place |

## 2. Method (identical for every config)

**Execution:**

- Decisions are made on closed bars only.
- Entry is at the open of the minute after the decision.
- Costs: Bybit taker 5.5 bp per side, plus slippage of 2 bp (BTC/ETH/SOL/XRP/BNB/DOGE) or 4 bp (others) per side.
- Real funding is charged or credited.

**Exits:**

- A guard stop of 1.5 × ATR(1h) × √(hold hours), clamped to 1–25%. This is the R unit.
- Stops are checked on 5m bars: stop first, and a gap fills at the bar open.
- Otherwise the trade exits at the time stop.

**Portfolio:** at most 6 positions, one per coin, each with notional equity/6.

**Selection (pre-registered):**

1. **DEV** (DEV1 + DEV2 pooled). A config becomes a candidate only if it has:
   - expectancy > 0;
   - daily t ≥ 2;
   - ≥ 200 trades;
   - expectancy > 0 at doubled costs;
   - for long-only configs, a result that beats the equal-weight long benchmark.
2. **VALIDATION.** A candidate passes only if it has:
   - daily t ≥ the Bonferroni z for 5%/m;
   - expectancy > 0 in both halves (split at 2025-07-01);
   - PF ≥ 1.10;
   - ≥ 150 trades;
   - expectancy > 0 at doubled costs.
3. **FINAL** (untouched 2020–21). A validated config must have expectancy > 0, t ≥ 2, and > 0 at doubled costs.

Nothing was re-tuned between stages.

**Grid:** 86 configs. That is 44 rules, 42 of which were also run with the C regime overlay (longs only while BTC is
above its 168h EMA and rising; shorts only while it is falling). The 2 benchmarks were run without it.

## 3. Results by family (expectancy in R per trade, after costs)

| Family | Configs | Best on DEV | DEV | VAL | FINAL | Conclusion |
|---|---|---|---|---|---|---|
| **A** cross-sectional momentum (L 4/24/72/168h, hold 4/24h, long-short / long-only / volume-confirmed / acceleration) | 40 | L72h H24h volume-confirmed + C | +0.067 (t 2.37) | +0.054 (t 2.25) | **−0.089 (t −1.66)** | **The only DEV candidate; failed FINAL** |
| **B** breakouts (Donchian 24/72h, volume filter, compression, failed-breakout fade) | 22 | Donchian 24h H24h vol1.5 + C | −0.009 | +0.003 | +0.022 | no edge; every 4h hold loses (t −3 to −10) |
| **C** regime overlay | 42 | (applied to all) | | | | It cuts losses roughly in half, but it doesn't turn any family positive on its own |
| **D** BTC→alt lead-lag (15m, hold 15/60 min) | 8 | k1.5 hold60m + C | −0.135 | −0.074 | −0.077 | **strongly negative**: the move is gone after costs |
| **E** volatility expansion (follow/fade, hold 4/24h) | 8 | follow H24h + C | +0.049 (t 1.64) | +0.032 (t 1.20) | +0.063 (t 1.67) | positive on every window, but never significant (see §6) |
| **G1** funding extremes (short high / long low rates around settlement) | 4 | H24h + C | +0.006 | −0.015 | +0.004 | no edge |
| **G2** OI divergence (DEV2 + VAL only) | 4 | fade covering H24h | +0.003 | −0.007 | n/a | no edge |

**Counts across the grid:**

- 11 of 86 configs are positive on DEV, and only 1 reaches t ≥ 2.
- 23 are positive on VAL.
- 21 of 82 are positive on FINAL.

**What the cost model shows:**

- Short holds (15m to 4h) lose in every family, every window and every year.
- Round-trip costs are about 15–19 bp. That is 0.05–0.3R, more than any raw signal earns at those horizons.
- The best 24h long-only momentum configs track the equal-weight long benchmark. In other words, they are market beta,
  not selection skill. They are positive in the 2020–21 and 2025–26 bull windows and negative in 2022.

## 4. The one candidate: why it was rejected

**Config:** A xs-mom L72h H24h volume-confirmed + C regime, with these parameters:

- every 24h, rank coins by their 72h return;
- long the top 3 and short the bottom 3, only where the coin's 24h volume is ≥ 1.3× normal;
- longs only in a BTC up-regime, shorts only in a down-regime;
- hold 24h.

| | DEV | VAL (H1 / H2) | FINAL |
|---|---|---|---|
| Trades | 423 | 573 | 138 |
| Expectancy | +0.067R, 95% CI [+0.015, +0.124] | +0.054R (+0.033 / +0.067), CI [+0.008, +0.097] | −0.089R, CI [−0.19, +0.017] |
| Daily t | 2.37 | 2.25 (Bonferroni z 1.64, m = 1) | −1.66 |
| Profit factor | 1.62 | 1.41 | 0.58 |
| Win rate | 49.6% | 51.8% | 36.2% |
| At 2× costs | +0.055R | +0.042R | −0.098R |
| Portfolio (sum, equity/6 sizing) | +77.9%, max DD 13.5% | +62.4%, max DD 28.1% | −46.0%, max DD 46.0% |
| By year | 2022 −0.017, 2023 +0.125, 2024 +0.050 | 2024 +0.043, 2025 +0.036, 2026 +0.080 | 2020 −0.025, 2021 −0.165 |
| By BTC regime | up +0.078, down +0.054 | up +0.056, down +0.050 | up +0.018, **down −0.242** |

It passed every DEV and VALIDATION check and then lost on the untouched final window. The loss is concentrated in the
2021 down-regime shorts: May–July 2021 squeezes ran against the bottom-momentum coins. A rule that only works in two
of three disjoint market eras is not a robust edge.

Under the pre-registration it is rejected. It is **not** promoted, and the rule is not altered to rescue it.

## 5. Statistical reliability

- With 86 configs, about 2 would reach DEV t ≥ 2 by chance alone. One did.
- Passing VAL on top of DEV is more than chance would usually give. FINAL then shows it doesn't generalise.
- Daily t-stats use every calendar day of the window (zero-trade days included). CIs are bootstrapped over days.
- Long-short and long-only versions of the same rule are highly correlated. Bonferroni over the candidates is therefore
  conservative, but with m = 1 it did not matter.

## 6. Observation (not a verdict): E vol-expansion follow, 24h hold

The rule: after a 1h bar whose range is ≥ 2.5 ATR on ≥ 3× volume, go with the close location and hold 24h. It is
positive on all three windows:

- DEV +0.049R, t 1.64;
- VAL +0.032R, t 1.20;
- FINAL +0.063R, t 1.67;
- still positive at 2× costs on all three.

It **failed** the pre-registered DEV threshold (t ≥ 2), and no window is individually significant. It was noticed only
after seeing every window, so no untouched history remains to test it. The only honest test left is a pre-registered
**forward** paper run on future data. It must not be promoted on these numbers.

## 7. Limitations

- **Survivorship:** each window uses coins that existed on the venue then, but the universe was chosen by today's list
  (the V11 30 coins). Dead coins from 2020–22 are missing, which biases results toward survivors.
- **FINAL is on a different venue:** Binance public archive, 16 coins. Costs were modelled at Bybit rates, and with
  fewer coins the momentum ranks are less extreme.
- **Data gaps:**
  - FINAL has no warm-up data before 2020-01, so roughly the first month has no signals;
  - G2 has no final stage;
  - F was not run.
- **Execution model:**
  - taker only;
  - slippage is a fixed estimate, not book depth;
  - no partial fills.

## 8. Recommendation per candidate

| Config | Recommendation |
|---|---|
| A xs-mom L72h H24h volume-confirmed + C | **Reject** (failed FINAL). Keep as a documented failed hypothesis. |
| E vol-expansion follow H24h (+C) | **Do not build.** Optionally pre-register a forward paper-only observation: fixed rule, 6 months, judged by the same thresholds. This requires the user's go-ahead. |
| Everything else | **Reject**: no DEV edge after costs. |

Renata V11.4 continues unchanged. No V12-RESEARCH bot exists.

## 9. Reproduce

```
PYTHONPATH=/data/pylib:. python scripts/v12_research.py run --window DEV1   # and DEV2, VAL, FINAL
PYTHONPATH=/data/pylib:. python scripts/v12_research.py select --out docs/V12_RESEARCH.json
python -m pytest -q tests/test_lab12.py
```
