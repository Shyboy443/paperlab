# Scribd round 6: the index mean-reversion family, out of sample (pre-registered 2026-10-21)

**Operator:** "keep going, we need a best return winner, refer the documents".
- Across rounds 1–5 (≈150 rules), the only rule that passed every check was round 3's `ibs_qqq`: buy QQQ when it
  closes in the bottom 10% of its range.
- This round looks for documents in that family (short-term mean reversion in index ETFs) and in trend-quality
  stock selection. Each is tested on its own markets, after its publication date where possible.

## Sources read

**Searches:** "SPY mean reversion strategy rules", "williams %R strategy S&P 500", "connors RSI strategy backtest ETF",
"nasdaq QQQ swing trading strategy", "VIX trading strategy SPY backtest", "buy the dip strategy backtest S&P". About 20
documents were opened. The rules were written down before any data was loaded.

**Not tested:**
- **No exact parameters:**
  - Mean Reversion with True Strength Index (828236708): its "bespoke oscillators" are not given;
  - AQR "Hold the Dip" (968528607): a research note comparing variants, not a rule set.
- **Not reproducible from its own text:** MR Swing, NAAIM 2010 (343596525). It combines three custom oscillators
  (DVO, Value Chart, SVAPO) with patient limit-order entries and exits.
- **Paid course:** Adam Khoo's Bollinger Mean Reversion lesson (635345637), for individual stocks with divergence
  judgement.
- **Marketing / stubs:** One Ticker Retirement Plan (694780950), Nasdaq Swing Trading (319012557), and the Williams %R
  report (383821211), which is a disclaimer and marketing.
- **Image-only / unreadable:** 251360667, 160096524, 475741163, 1033990399, 37344845.

## Rules tested (`app/ytstrats/round6.py`)

| Key | Document | Markets named → tested on | Bars |
|---|---|---|---|
| `pagonidis_ibs_1day` | The IBS Effect: Mean Reversion in Equity ETFs, Pagonidis 2013 (168296451), Table 6 strategy | the paper's 33 ETFs (Table 1); RSX is closed and has no data | daily |
| `pagonidis_rsi3_ibs` | the same paper, Cutler RSI(3) strategy with the IBS ≤ 0.5 filter (Table 16) | the same 33 ETFs | daily |
| `katsanos_stiffness` | The Stiffness Indicator, Katsanos, S&C Nov 2018 (422943971) | S&P 500 stocks → the 30 Dow stocks (survivorship-biased) | daily |

### Exact rules (fixed now)

- **`pagonidis_ibs_1day`:**
  - IBS = (close − low) / (high − low).
  - IBS ≤ 0.2 → long at the close; IBS ≥ 0.8 → short at the close. Each is closed at the next close (one day,
    close to close).
- **`pagonidis_rsi3_ibs`** (long only):
  - **Cutler's RSI(3):** the simple 3-day averages of up and down closes; 100 when there were no down closes.
  - **Entry:** buy at the close when RSI(3) < 10 and IBS ≤ 0.5.
  - **Hold:** keep the position while RSI(3) ≤ 40 and IBS ≤ 0.5, checked at each close. Otherwise exit at that close.
- **`katsanos_stiffness`** (long only):
  - **Stiffness:** 100 × (the number of the last 60 closes above SMA100 − 0.2 × the 100-day standard deviation) ÷ 60.
  - **Entry:** buy at the next open when stiffness crosses above 90 and SPY's EMA(100) is above yesterday's value.
    The article's garbled "EMA(SPY,100) > EMA(SPY,100)" is its rising-EMA condition.
  - **Exit:** at the next open when stiffness crosses below 50, or after 84 bars in the trade.

## Data, costs, windows

- **Data:** Yahoo Finance daily, adjusted.
- **Costs per side:**
  - SPY, QQQ, IWM: 0.01% (as in round 3);
  - the country and regional ETFs: 0.05%;
  - stocks: 0.03%.
- **Windows:**

  | Rule | A (the document's own sample) | B (after publication) |
  |---|---|---|
  | Pagonidis rules | the ETF's start → 2013-01-01 (the paper's data ends 2012-12-31) | 2013-01-01 → 2026-10-21 |
  | Stiffness | 2008-08-01 → 2018-08-01 (the article's 10-year test) | 2018-08-01 → 2026-10-21 |

## Decision rule (fixed now)

Per-trade returns in % after costs. A rule passes only if all of these hold:

1. Window A: mean > 0 and PF ≥ 1.10.
2. Window B: mean > 0, PF ≥ 1.15 and ≥ 100 trades.
3. Window B: the lower end of the bootstrapped 95% interval of the mean is above 0.
4. Window B: positive on at least half of its markets.

A stock pass counts only after a survivorship-free re-test (as in round 2).

**Reported but not part of the verdict:**
- **For the long-only rules:**
  - a random-timing comparison: the same holding times starting on random days, in the same markets;
  - the compounded return of an equal-weight portfolio.
- **For `pagonidis_rsi3_ibs`:** the result on QQQ alone, and its signals traded in TQQQ (as for `ibs_qqq` in round 4).

**A random-walk check of each simulator** is run before any real data. A pass is a candidate for a forward paper
test. Nothing is changed after the results are seen.

## Random-walk check (before real data)

Zero cost, daily price martingale with 400 steps per candle:

| Rule | z |
|---|---|
| `pagonidis_ibs_1day` | −0.3 |
| `pagonidis_rsi3_ibs` | +1.3 |
| `katsanos_stiffness` | +1.4 |

## Result (run 2026-10-21): no new counted winner

`app/ytstrats/round6_results.json`, served at `GET /api/public/scribd-round6`. GXG and RSX have no Yahoo data (closed),
so 31 of the paper's 33 ETFs are tested.

| Rule | A: trades / mean / PF | B: trades / mean / PF | B before costs | Verdict |
|---|---|---|---|---|
| IBS one-day, 31 ETFs | 50,309 / +0.158% / 1.27 | 50,585 / −0.014% / 0.97 | +0.079% | FAIL |
| Cutler RSI(3) + IBS, 31 ETFs | 6,546 / +0.425% / 1.64 | 6,871 / +0.060% / 1.10 | +0.152% | FAIL (B: PF < 1.15) |
| Stiffness, Dow 30 | 98 / +7.06% / 4.37 | 226 / +2.90% / 1.63 | +2.96% | PASS, not counted (survivorship) |

**Reading.**
- **The IBS effect weakened after its 2013 publication.**
  - The one-day rule kept half its edge before costs (+0.16% → +0.08% per trade).
  - That is no longer enough to pay 0.05% per side on country ETFs.
- **The RSI(3) + IBS rule still beats random timing after publication** (+0.095% per trade, z 4.3), but misses
  the PF bar across 31 ETFs.
  - On QQQ alone (not part of the verdict): window B 215 trades, +0.45% per trade, PF 2.08.
  - Its QQQ signals traded in TQQQ, 2010–2026: 19.3% a year, 31% max drawdown, worst trade −22.8%.
  - That is weaker than round 3's `ibs_qqq` in TQQQ (37.6% a year, 35%).
- **Stiffness's pass is the Dow stocks' own rise.** It does worse than random entry days after 2018 (−1.8% per
  trade, z −1.7) and was tested on a survivorship-biased list.

**Best return winner after six rounds** (unchanged): round 3's `ibs_qqq`.
- QQQ: PF 1.80 / 2.88 in its two windows; PF 3.03 after its article.
- Traded in TQQQ: 37.6% a year since 2010, with a 35% drawdown at trade closes.
