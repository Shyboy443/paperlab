# Scribd scalping round 2 (pre-registered 2026-10-20)

**Operator:** "i need scalping strategies".
- Round 1 (docs/SCRIBD_SCALPING_STUDY.md) tested 35 scalping rules from the first Scribd results pages; all were at
  zero before costs, so none passed.
- This round reads only scalping documents not read before. It tests them on about six years of the operator's
  1-minute MT5 history (2020-11 → 2026-10) instead of two.

## Sources read

**Searches:** "1 minute scalping strategy", "NAS100 US30 scalping strategy", "crypto scalping strategy", "VWAP scalping
strategy". About 25 new documents were opened, skipping every ID already read in rounds 1–4. The rules were written
down before any data was loaded.

**Not tested:**
- **A private indicator:** the Reddit "How I scalp the 1-minute US30" post (692785864), which needs its authors' own
  divergence scanner.
- **Paid indicators:** US30 Telegram notes (682410771), built on "Agimat" / "Simplicity" / "Zero Hero".
- **Discretionary / order flow:**
  - Drysdale VWAP protocol (1073463325), using cumulative delta, balance/imbalance judgement and volume profile;
  - Scalping the Open webinar (634751015);
  - crypto Tradernauts ebook (982182437), Wyckoff / smart money.
- **No data:**
  - Premarket Gap Scalping (984833052): news-driven small caps, premarket;
  - 5 EMA Strategy (675698809): Bank Nifty intraday.
- **Books (> 40 pages):** NAS100 & US30 guide (641754698) and High Probability Scalping Playbook (880862988).
- **Image-only:** 961265884.

## Rules tested (`app/ytstrats/scalp2.py`)

**Shared conventions:**
- Signals are read on completed candles of the rule's timeframe, built from 1-minute candles.
- Entries fill at the next 1-minute open, or at a stop-entry level when the rule uses one.
- Stops, targets, partials, breakeven moves and trailing stops are walked on **1-minute candles**, using the neutral
  OHLC path. Round 2 showed that 5-minute candles fake about 2 pips on tight stops.
- One position at a time per market.
- A document that names no market is tested on the 7 FX majors (the convention of every earlier round).
- Gold: 1 pip = 0.10 USD.

| Key | Document | Market → tested on | Signal bars |
|---|---|---|---|
| `xau_vwap_band` | XAUUSD High-Frequency Scalping, Setup 1 (977479117) | XAUUSD | 3m (bias 15m) |
| `vwap4h_early` | 4H Anchored VWAP Scalping, Strategy A (977479114) | none named → 7 FX majors | 3m |
| `gbpjpy_beast` | GBP/JPY 10 Pips Scalping "Beast Mode" (848267234) | GBPJPY | 1m (trend 5m) |
| `asr_vwap_macd` | ASR VWAP Scalping Strategy (760301637) | none named → 7 FX majors | 15m |
| `codetrading_vwap_bb` | VWAP_Scalping_01 notebook (932043376) | EURUSD (its data file) | 5m |
| `smb_vwap_flip` | SMB Capital "Offsides" scalping, trigger #2 VWAP flip (890912538; font-scrambled) | YM (Dow) → US30 | 5m |
| `five_step_ema` | A 5 Step Scalping Strategy (754883344) | none named → 7 FX majors | 5m (anchor H1) |

### Exact rules (fixed now). Longs are described; shorts mirror them.

- **`xau_vwap_band`:**
  - **VWAP:** volume-weighted from the London anchor (03:00 New York) until 08:00 New York, then re-anchored at 08:00
    New York (the NY anchor) until 17:00. Trades only 03:00–17:00 New York.
  - **σ:** the volume-weighted standard deviation of the typical price since the anchor.
  - **Bias:** on the last completed 15m candle, close > VWAP and EMA9 > EMA21 (15m).
  - **Setup:** a 3m candle whose low reaches VWAP − 1σ or lower. It must be bullish with a body > 50% of its range,
    its volume must exceed the average of the 3 candles before it, and RSI(7) must be rising after a value < 30
    within the last 3 candles. Buy at the next open.
  - **Stop:** 10 pips (1.00 USD) below the signal candle's low.
  - **Targets:** 70% at the VWAP; 30% at VWAP + 1σ (levels fixed at the signal).
  - **Breakeven:** once the price is 8 pips in profit, the stop moves to entry + 2 pips.
  - Closed at 17:00 New York. Skipped if the entry is already at or above the VWAP.
- **`vwap4h_early`:**
  - **VWAP:** anchored at every 4-hour UTC boundary (00, 04, 08, 12, 16, 20). Signals only in the first 90 minutes.
  - **Setup:** a 3m candle whose low reaches VWAP − 1.5σ or lower, while the low since the anchor is still above
    the previous 4-hour candle's low. It must be bullish with a body > 50% of its range, its volume must be at least
    1.5× the average of the previous 20 candles, and RSI(7) must be rising after a value < 25 within the last 3
    candles.
  - **Exits:** stop 10 pips below the signal low; 70% at the VWAP, 30% at VWAP + 1σ.
  - Closed at the end of that 4-hour period.
  - The "4H candle showing bullish potential" judgement is dropped.
- **`gbpjpy_beast`:**
  - **Hours:** London 07:00–10:00 London time and New York 08:00–11:00 New York time.
  - **Trend:** the last completed 5m EMA50 is above its value 3 candles (15 minutes) earlier.
  - **Setup (1m):**
    - a candle in the last 3 touched the 1m EMA20 (low ≤ EMA20 ≤ high);
    - RSI(14) is between 40 and 60 and rising;
    - a bullish engulfing candle: prior candle bearish, this one bullish, close > prior open, open ≤ prior close.
  - Buy at the next open. Stop 11 pips, target 10 pips.
  - Two losses in a row end the day.
- **`asr_vwap_macd`:**
  - **VWAP:** daily, anchored at the broker day start (17:00 New York).
  - **Setup (15m):** close > EMA200, and on the same candle the close crosses above the VWAP (the previous close was
    at or below it) and the MACD(12,26,9) line crosses above its signal line. Buy at the next open.
  - **Exits:** stop at the signal candle's low; half at 1R, half at 2R.
- **`codetrading_vwap_bb`:**
  - **VWAP:** daily, anchored at 00:00 UTC (pandas_ta's default).
  - **Up-trend:** the bodies (min(open, close)) of the signal candle and the 15 before it all lie above the VWAP.
  - **Setup:** an up-trend, plus close ≤ the lower Bollinger band(14, 2) and RSI(16) < 45. Buy at the next open.
  - **Exits:**
    - stop at close − 1.2 × ATR(7);
    - target at close + 1.5 × 1.2 × ATR(7);
    - exit at the next open when RSI(16) ≥ 90.
- **`smb_vwap_flip`:**
  - **VWAP:** anchored at 18:00 New York (the futures session).
  - **Hours:** signals 05:00–12:00 New York, none between 09:30 and 09:45.
  - **Setup:** the 5m candle closes above the VWAP after the previous close was below it. The candle must be bullish
    with a body ≥ 60% of its range ("decisive, without a lot of wicks"). Buy at the next open.
  - **Exits:** stop at the lowest low of the 6 candles before the signal candle ("recent low prior to the flip");
    target 1R.
  - The discretionary "offsides" volume-divergence trigger is not tested.
- **`five_step_ema`:**
  - **Anchor:** on the last completed H1 candle, EMA8 > EMA21, both above their previous values, and close above
    both.
  - **Trigger:** on 5m, EMA8 > EMA13 > EMA21 and a candle touches EMA8 (low ≤ EMA8).
  - **Entry:** a buy stop 3 pips above the highest high of the 5 candles before the trigger, live for the next 6
    candles (my bound; none is given).
  - **Stop:** 3 pips below the trigger candle's low.
  - **Targets:** half at 1R, then the stop moves to breakeven; half at 2R.

## Data, costs, windows

- **Data:** the operator's MT5 (Fusion Markets) 1-minute history, converted to UTC. New York and London times use the
  US/UK daylight-saving calendars. Real 1-minute data begins in late 2020.
- **Costs per side:** half the mean recorded spread + 0.002% slippage, + 0.0025% commission on FX and gold (none on
  US30).
- **Windows:** A = 2020-11-01 → 2023-11-01, B = 2023-11-01 → 2026-10-21.

## Random-walk check (run before any real data)

Zero costs on a driftless 1-minute random price martingale with real session clocks and random volume. The expected
mean per trade is zero.

| Rule | z on random walks |
|---|---|
| `xau_vwap_band` | −0.5 |
| `vwap4h_early` | +0.5 |
| `gbpjpy_beast` | −0.2 |
| `asr_vwap_macd` | +0.2 |
| `codetrading_vwap_bb` | +1.2 |
| `smb_vwap_flip` | −0.4 |
| `five_step_ema` | **−1.8 to −3.6** |

- **The 5-step EMA scalp keeps a small negative bias,** about −0.005% to −0.015% per trade. The bias is *against*
  the rule, so a pass would stand; a fail within that margin is noted in the results.
  - The trade engine alone is unbiased: random entries with symmetric stops and targets read z +0.3 to +0.9.
  - The bias comes from how stop entries interact with 1-minute candles. Making the fill candle go straight from the
    fill to its close (a method change made here, before real data) did not remove it.

## Decision rule (fixed now)

Per-trade returns in % of price, after costs. A rule **passes** only if all of these hold:

1. Window A: mean > 0 and PF ≥ 1.10.
2. Window B: mean > 0, PF ≥ 1.15 and at least 100 trades. The minimum is 30 for the single-market documents:
   `xau_vwap_band`, `gbpjpy_beast`, `codetrading_vwap_bb` and `smb_vwap_flip`.
3. Window B: the lower end of the bootstrapped 95% interval of the mean is above 0.
4. Window B: positive on at least half of its markets.

**Also reported:**
- the result before costs;
- trades per day;
- the return per year at 1× notional.

A random-walk check of every simulator is run before any real data. A pass is a candidate for a forward paper test.
Nothing here is changed after the results are seen.

## Result (run 2026-10-20): 0 of 7 pass. Every scalp is at zero before costs and loses after.

`app/ytstrats/scalp2_results.json`, served at `GET /api/public/scribd-scalp2` ("Scalping round 2" tab). Per-trade
results in % of price.

| Rule | A: trades / mean / PF | B: trades / mean / PF | B before costs | Trades/day per market | Verdict |
|---|---|---|---|---|---|
| Gold VWAP-band scalp (XAUUSD 3m) | 70 / −0.021% / 0.49 | 80 / −0.013% / 0.58 | −0.003% | 0.11 | FAIL |
| 4H-anchored VWAP (7 majors, 3m) | 1,431 / −0.009% / 0.70 | 1,305 / −0.009% / 0.66 | +0.002% | 0.25 | FAIL |
| GBPJPY "Beast Mode" 10 pips (1m) | 3,766 / −0.015% / 0.63 | 3,582 / −0.014% / 0.58 | +0.001% | 4.79 | FAIL |
| ASR VWAP + MACD (7 majors, 15m) | 3,482 / −0.013% / 0.82 | 3,292 / −0.012% / 0.79 | −0.001% | 0.63 | FAIL |
| CodeTrading VWAP + Bollinger (EURUSD 5m) | 2,075 / −0.005% / 0.83 | 2,136 / −0.007% / 0.72 | +0.002% | 2.85 | FAIL |
| SMB VWAP flip (US30 5m) | 1,074 / −0.015% / 0.88 | 1,132 / −0.009% / 0.91 | −0.001% | 1.51 | FAIL |
| 5-step EMA scalp (7 majors, 5m) | 17,475 / −0.020% / 0.75 | 15,544 / −0.020% / 0.73 | −0.009% | 2.97 | FAIL |

**Reading.**
- **Before costs, every rule is within ±0.01% per trade.** On EURUSD that is about one pip either way, the same as
  scalping round 1.
- **The 5-step EMA's −0.009% before costs lies inside its known random-walk bias** (about −0.005% to −0.015%). Even
  without that bias it would sit at zero, below its costs.
- **The win rates are what each rule's reward:risk gives at random.** Examples:
  - GBPJPY 53% with a 10-pip target and an 11-pip stop;
  - SMB 50% at 1R;
  - the VWAP-band rules 59–69% with targets closer than their stops.
- **Costs then decide the outcome.**
  - The spread plus commission is about 0.008–0.015% of the price per round trip on FX and gold.
  - Once costs exceed a near-zero edge, every rule loses.
- **Across both scalping rounds (42 rules, from 1-minute to 15-minute charts), no Scribd scalping document has
  shown an edge.**
