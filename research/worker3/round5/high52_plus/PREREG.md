# high52_plus: pre-registration (Worker 3, round 5)

Written 2026-10-27, BEFORE any TEST or HOLDOUT run of a new variant. DEV (2021-01..2024-10, perp panel with funding) is
the only data used to choose anything. TEST (2024-11..2026-10) and HOLDOUT (2018-01..2020-12, Binance spot; shorts are
assumed possible on spot) are each run ONCE, for the ONE selected config, by `final.py`.

## Idea and sources
The lead is high52 (round 2/4): rank the top-40 coins by where yesterday's close sits inside its 30-day high-low range
(Donchian position, a short-window "nearness to the high"), long the top 10 / short the bottom 10, daily rebalance, 1x.

- George & Hwang (2004), "The 52-Week High and Momentum Investing", *Journal of Finance* 59, 2145-2176: nearness to the
  52-week high forecasts returns better than past returns, and the effect does not reverse (anchoring story).
- Jia, Simkins, Yan, Zhang & Zhao, "Psychological Anchoring Effect and Cross Section of Cryptocurrency Returns"
  (SSRN 5386180; summary at https://vvv.quantpedia.com/?p=42289): nearness to the 52-week high predicts crypto
  cross-sectional returns; a long-short cANCHOR factor earns about 1.3%/week (as summarised; full text not checked).
- Zarattini, Pagani & Barbon, "Catching Crypto Trends: A Tactical Approach for Bitcoin and Altcoins"
  (https://ideas.repec.org/p/chf/rpseri/rp2580.html, https://abarbon.com/papers/catching-crypto-trends): an ensemble of
  Donchian channels with several lookbacks on a rotating top-20 liquid-coin universe, with trend-following exits; Sharpe
  above 1.5 net of fees in their sample. Motivates the blended-channel score (BL) and the channel-based exit (C).
- Cooper, Gutierrez & Hameed (2004), "Market States and Momentum", *Journal of Finance* 59, 1345-1365: momentum profits
  appear only after UP markets. Motivates the BTC trend gate. Li, Urquhart, Wang & Zhang, "MAX momentum in cryptocurrency
  markets" (https://research.birmingham.ac.uk/en/publications/max-momentum-in-cryptocurrency-markets/) also report
  crypto momentum strength varying with market state.

## Insights taken from earlier TEST/HOLDOUT reports, re-verified on DEV
Taken from round 4's DEV-window diagnostics (`round4/high52_v2/learn.json`, `diagnostics_ORIG.DEV`; DEV data only), and
re-printed by `dev.py`:
1. Long leg carries the profit on DEV: longs 97.2% of profit, shorts 3.6%; long leg alone (0.5 gross) net +381%,
   short leg alone -57%. VERIFIED on DEV. But the long leg alone has a 62% max DD even at 0.5 gross: the short leg is a hedge.
2. BTC regime on DEV: book's mean day with BTC > SMA100 = +5.4%/month (t 2.96); BTC <= SMA100 = +2.3%/month (t 1.17).
   VERIFIED (weaker, not zero, in down-regimes).
3. Riding winners on DEV: positions held > 10 days = +344% of profit, held <= 3 days = -371%. VERIFIED.
Used: all three, as motivation for the books (long-only / gated / tilted), the exits (H, C) and the gate.

Disclosure: while building the engine I ran two grid members on DEV as smoke tests (GL/H/30 and GC/C/BL; both poor,
DEV DD ~60%). They are counted in the grid below. The engine reproduces round-4 ORIG exactly (DEV net +383.26%,
holdout +244.72%).

## The DEV grid (total 40 configs)
Fixed unless stated: top-40 universe (30-day quote volume, point-in-time, incl. delisted perps), k = 10 per leg, gate =
yesterday's BTC close vs its 100-day SMA, retention universe for H/C = top-80, 1% of previous-day volume cap per position.

Stage 1, full factorial, 6 x 3 x 2 = 36 configs:
- book: LS (ORIG 10/10, 0.5 gross per leg) | LO (long-only 1x) | GC (gate up: long-only 1x, down: cash) |
  GL (gate up: long-only 1x, down: LS) | GLS (gate up: LS, down: cash) | TILT (gate up: long 2/3 + short 1/3, down: LS)
- exit: D (daily full rebalance to the top/bottom k, = ORIG) | H (rank hysteresis: enter top-k, hold while rank <= 2k;
  sizes drift, new entries at equal weight within the leg's remaining budget) | C (channel exit: enter top-k, hold a
  long while its Donchian position >= 0.5 (above mid-channel), a short while <= 0.5; sizes drift)
- channel: 30 (single 30d) | BL (mean Donchian position over 20/30/60/120 days)

Stage 2, applied to the stage-1 winner only, 4 configs:
- universe top-20 with k = 5;  universe top-80 with k = 10;
- volume-confirmed entries (5-day mean quote volume > 30-day mean);
- trim: drifting positions capped at 2x equal weight (a no-op under exit D; still counted).

## Selection rule (DEV only)
Eligible: DEV net at doubled costs > 0; DEV max DD <= 35%; DEV months up >= 23/46; largest single position <= 10% of DEV
net profit. Stage-1 winner = highest DEV geo month among eligible (ties: higher DEV t). A stage-2 variant replaces it
only if it is eligible and its DEV geo month is at least +0.20 pp higher; if several, the highest geo.
If nothing in stage 1 is eligible, relax to max DD <= 40% and largest position <= 15%; if still none, the result is FAIL
with ORIG kept as reference (TEST/HOLDOUT are then run for the best relaxed-rule config for information only).

## Neighbour grid for check 5 (selected config)
Each at 4 values: channel lengths x {0.5, 0.75, 1.25, 1.5}; k {5, 8, 12, 15}; decision every {2, 3, 5, 7} days;
universe {20, 30, 50, 60}; plus, if the config is gated, gate SMA {50, 75, 150, 200}; if exit H, hysteresis
{1.5, 1.75, 2.5, 3.0} x k; if exit C, exit level {0.25, 0.375, 0.625, 0.75}. Pass on TEST = >= 70% of neighbours net
positive on both DEV and TEST; pass on HOLDOUT = >= 70% net positive on the holdout.

## Checks (research/monthly20/ROBUSTNESS.md)
"TEST window" = the checks exactly as written (DEV + TEST, 2021-01..2026-10). "HOLDOUT" = the round-4 adaptation:
1 net > 0 at 1x and 2x costs; 2 >= 2 of 3 years positive and 2019-2020 positive; 3 t >= 2 and block-bootstrap p5 > 0;
4 positive without the 3 best months; 5 neighbours; 6 beats >= 95% of 300 random-score books (daily redraw AND 30-day
block redraw) run through the SAME config (gate, exits, sizing); 7 positive without the top-5 coins and no position
> 10% of profit; 8 geo > 0 and geo >= half the mean month.

## Portfolio
W1 / W2 monthly series as in round 4 (holdout: worker1_backfill, worker2/backfill; 2021-26: r4_07_ensemble,
worker2/session). Report W1+W2+X in thirds, and the best DEV-chosen mix from weights on a 10% grid (each >= 10%)
chosen by DEV geo month subject to DEV monthly DD <= the W1+W2 50/50 DEV monthly DD; that mix is then applied unchanged
to TEST and HOLDOUT.

## ADDENDUM: DEV result and the ONE selected config (written after dev.py, before final.py)
`dev.py` ran 40 configs (36 stage 1 + 4 stage 2), all on DEV only; full table in `dev.json` / `dev.log` / REPORT.md.
- Strict rule: NO stage-1 config eligible (ORIG fails only on largest position 11.8% > 10%; every long-only / gated /
  drift-exit book has DEV DD > 35% or months up < 23 or position share > 10%, except GLS-D-30 at 18/46 months up).
- Relaxed rule (DD <= 40%, position <= 15%): only LS-D-30 (= ORIG) eligible -> stage-1 winner.
- Stage 2: top-20 / k = 5 raises DEV geo 3.48 -> 5.13 (+1.65 pp), eligible (DD 26.3%, 27/46 up, position 11.3%,
  2x costs +521%). Top-80, volume confirmation and trim did not qualify (trim = no-op under D, identical to ORIG).

**SELECTED: book LS (5 long / 5 short, 0.5 gross per leg), exit D (daily full rebalance), 30-day Donchian position,
universe top-20 by 30-day quote volume, 1x.** Because the relaxed rule was needed, the selection itself is flagged as
weaker than planned. Disclosure: round 2 / round 4 had already run "univ 20 with k = 10" as a check-5 neighbour on TEST
and HOLDOUT (both positive); the selected top-20/k=5 config was not run on TEST or HOLDOUT before now.

Check-5 neighbours, re-scaled around the selected base by the ROBUSTNESS.md rule (about -50/-25/+25/+50%), since the
absolute values listed above assumed k = 10 / top-40: channel 30 x {0.5, 0.75, 1.25, 1.5} (15/22/38/45 days);
k {3, 4, 6, 8}; decision every {2, 3, 5, 7} days; universe {10, 15, 25, 30}. 16 neighbours. Not gated, so no gate
neighbours. Doubled costs, random books (300 + 300) and top-5-coin removal as above.
