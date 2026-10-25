# Scribd scalping strategies: which ones hold up? (pre-registered 2026-10-20)

**Operator's request:** "in my scribd there are so many strategies to scalp, test everything and find the best returning
winners that consistently work".

## Sources

- **Read:** Scribd search "scalping strategy" (first page, 40 documents) and "gold scalping strategy" (gold-specific
  documents), read in the operator's logged-in browser, about 54 documents.
- **Rule notes:** the rules were written down in my own words before any test, in a working file outside the repo.
- **Left out:**
  - full books (likely unauthorised uploads);
  - image-only documents;
  - strategies needing proprietary indicators or robots;
  - general advice with no rules;
  - duplicates.
- **Discretionary rules are not tested.** These are hand-drawn S/R, trendlines, SMC order blocks judged by eye, and
  undefined thresholds. They are listed as such in the results and not guessed at.
- **35 mechanical rules are tested:** `app/ytstrats/scribd.py`. Each docstring is the rule as tested. Rules from
  font-scrambled documents were decoded by letter substitution. One decoding (Malay gold) was confirmed exactly by a
  clean copy found later.

## Fixed before any result was seen

- **Markets:** only the market a document names.
  - "Forex" with no pair named means the 7 majors.
  - Named pairs mean only those pairs.
  - Gold means XAUUSD.
  - Bitcoin means BTC.
  - "Crypto" means the 20 Binance perpetuals of the previous study.
- **Gold pip:** 0.10 USD, which the documents' own examples confirm.
- **Data:**
  - Forex and gold: the operator's MT5 (Fusion Markets), converted to UTC.
  - Crypto: Binance USD-M perpetuals.
- **Costs per side** (a share of the price):
  - MT5: half the mean recorded 15-minute spread, plus 0.002% slippage, plus 0.0025% commission.
  - Crypto: 0.05% taker plus 0.02% slippage.
- **Execution** (`app/ytstrats/scalp.py`):
  - A signal at a close fills at the next open, or at a stop-entry level when the rule uses one.
  - One position per asset.
  - Inside a candle the price travels open → low → high → close for a green candle and open → high → low → close for
    a red one. This is the standard neutral OHLC path, and the first level reached wins.
    - **Method change, made before the full run and not tuned to any result:** the first draft counted a candle
      touching both the stop and the target as a loss. A random-walk check showed that this biases rules with stops of
      2–10 pips strongly negative (z down to −19.6). With the neutral path, 33 rules average z = +0.17 on random data.
    - The Malay gold scalp's 2.5-pip stop sits inside one candle's normal range and still reads z −2.8 on random
      data. A tick-level check would be needed before trusting a pass there.
  - When a document gives no exit, the rule's own opposite signal closes and reverses ("no exit given" in the notes).
- **Windows:** the MT5 history is capped by the terminal's "Max. bars in chart" (100,000).
  - **15m and 1h rules:** year 1 2024-10-20 → 2025-10-20 and year 2 2025-10-20 → 2026-10-20. This is the full verdict.
  - **5m rules:** the available data (2025-06-17 → 2026-10-19), split into window A before 2026-02-19 and window B
    from it on. This is PRELIMINARY.
  - **1m rules:** 2026-07-14 → 2026-10-19, split into window A before 2026-09-01 and window B from it on. This is
    PRELIMINARY.
  - Crypto rules: the full two years from Binance.
  - Once the operator sets the cap to Unlimited, the 1m and 5m rules are re-run on the full two years, with the same
    code and rules.

## Decision rule (fixed now): returns per trade in % of price, all costs

A rule **passes** only if all of these hold:

1. First window: mean > 0 and profit factor ≥ 1.10;
2. Second window: mean > 0, profit factor ≥ 1.15 and at least 100 trades (at least 50 for a PRELIMINARY 1m window);
3. Second window: the lower end of the bootstrapped 95% interval of the mean is above 0;
4. Second window: positive on at least half of the markets it trades.

**Multiple testing:** with 35 rules tested, one or two could pass by chance. A pass makes a rule a **candidate for a
forward paper test**, never a live bot directly. Nothing here is changed after the results are seen.

## Result (run 2026-10-20): 0 of 35 pass

`app/ytstrats/scribd_results.json`, served at `GET /api/public/scribd-strategies`.

- **Before costs, every rule is at zero.** The mean result per trade in window B lies between −0.012% and +0.012% of
  the price. On EUR/USD that is about one pip either way.
- **After costs every rule loses.** Costs are about 0.01% per round trip on FX and gold, and 0.14% on crypto.
- **The win rates are what each rule's reward:risk gives at random.** Some are low: 15% for the 50/15/10 SMA stop-and-
  reverse, 10–17% for MACD + Stochastic with its 15–25 pip targets. Some are high: 57% for the Bollinger range fade,
  59% for PSAR + MACD with its small targets. None of them pays after costs.
- **Full two-year verdicts (15m and 1h): all fail.**
  - Fielder's dead-time range: PF 0.99 in year 1, 0.89 in year 2.
  - Fielder's session-open breakouts: PF 0.88 / 0.78 and 0.83 / 0.73.
  - The 14/50 EMA cross on USDJPY, AUDJPY and GBPJPY: PF 0.70 / 0.69.
  - Scalping Sheriff on 20 cryptos: PF 0.91 / 0.90.
  - Bitcoin MFI(3): PF 0.29 / 0.27.
- **The 5m and 1m verdicts are preliminary** (16 months and 3 months of data, see Windows), but none comes close:
  - The best is the gold EMA9/20 + RSI + MACD on 5m: +0.025% per trade in window A, +0.0015% in window B (PF 1.01).
    It fails on its second window.
  - "High-Probability Gold Scalping" made only 1–2 trades: its RSI-above-30 trigger almost never happens in its own
    required uptrend.
- **Conclusion:** none of the Scribd scalping documents holds up on the markets they name, with real costs. The 1m/5m
  re-run on full history, once MT5's bar cap is lifted, is a formality. It cannot turn an edge of zero before costs into
  a profit after them.

## Final result (2026-10-20): full two years for every rule, 0 of 35 pass

MT5's bar cap was raised to 5,000,000 and two years of 1m and 5m history were exported (about 760,000 1m bars per
market). Every rule was then re-run on year 1 2024-10-20 → 2025-10-20 and year 2 2025-10-20 → 2026-10-20, with the same
code and rules.
- **None passes.** The preliminary verdicts above are confirmed.
- **Before costs, year 2 is within −0.01% to +0.03% per trade for every rule.**
- **The only rule with a positive year 2 is the gold EMA9/20 + RSI + MACD on 5m:** +0.019% per trade, PF 1.13. It lost
  in year 1 (PF 0.96) and is below the PF 1.15 bar, so it fails.
- With 35 rules tested, one looking like this by chance is expected.
