# PREREG - r3 regime-gated momentum rotation (written 2026-10-23, BEFORE any gate is computed on any window)

## Honest disclosure up front
The author of this file already knows the always-on rotation lost about 27% in 2026 (Jan +28, Feb -18, Mar +12, Apr -24,
May -18, Jun +8, Jul -2, Aug -1, Sep +5, Oct -9) and the r2 results (DEV/TEST nets, 7/8 checks). No gate below has been
computed on any data before this file was written. The candidates are chosen from the four families the user named, with
round, textbook thresholds, not tuned. Selection uses DEV only (2021-01-01..2024-10-31). TEST (2024-11-01..2026-10-21)
is run once, for ALL candidates, after selection. Nothing in this file is edited after the DEV run.

## Base book (frozen = r2_rotation main candidate, unchanged)
Top-40 USDT perps by trailing 30-day quote volume (survivorship-free archive panel, 683 coins incl. delisted),
score = 14-day return known at the open, long top 10 / short bottom 10, 0.5x per leg (1x gross), rebalance every 7 days
at 00:00 UTC, costs 0.05% fee + 0.02% slip per side, archive funding, 1% volume cap, liquidation rule. Engine = r2's
engine_r2.run with ONE addition: a per-bar size multiplier s[i] in {0, 0.5, 1} applied to every target at a rebalance
bar i. s = 0 closes everything (exit costs paid) and the book sits in cash (no funding, no P&L) until the next
rebalance with s > 0, where it re-enters (entry costs paid).

## Gate timing (all candidates)
A gate is evaluated only at the book's own rebalance bars. At rebalance bar i (open of UTC day i) it may use closes up to
day i-1 and nothing later. "Universe at i" = the same top-40 eligible set the book uses at i. Until a gate has enough
history (stated per gate) it defaults to ON. All candidates: OFF = flat in cash (no half-size candidate).
BTC series = BTCUSDT perp daily closes from the shared cache (data/monthly20_cache/perp/1d/BTCUSDT.json, from 2020-01-01;
identical to the panel's BTC column where both exist).

## The 6 candidates (no others will be run before TEST)
1. **DISP** (dispersion). x_d = interquartile range (75th - 25th percentile) of the 14-day returns (the book's own score at d)
   across the universe at d. ON if x_i >= 1.0 x median(x over the trailing 365 daily values d = i-364..i).
   Needs >= 60 values, else ON.
2. **MOM8** (momentum of momentum). A = equity of the always-on base book run continuously from 2021-01-01 (baseline costs).
   ON if A(open of i) / A(open of i-56) - 1 > 0 (the always-on book's last 8 weeks were positive). Needs 56 days of book, else ON.
3. **BTC100** (market trend). ON if BTC close[i-1] > simple average of BTC closes over days i-100..i-1.
4. **BTCVOL** (volatility regime). v_d = standard deviation of BTC daily log returns over the 30 days ending d-1.
   ON if v_i >= 1.0 x median(v over the trailing 365 daily values). Needs >= 60 values, else ON.
5. **BREADTH** (directional market). b = share of the universe at i whose close[i-1] > their own 50-day simple average.
   ON if b >= 0.60 or b <= 0.40 (a clear up or down market); OFF in the mixed middle.
6. **RANKCORR** (reversal risk). rho_d = Spearman rank correlation, across the universe at d, between the last 14-day return
   (close[d-1]/close[d-15]) and the previous 14-day return (close[d-15]/close[d-29]). ON if the mean of rho at
   i, i-7, i-14, i-21 > 0 (ranks have been persisting, not flipping). Needs all 4 values, else ON.

## Winner rule (DEV only, decided before seeing any DEV number)
For each candidate, run DEV 2021-01-01..2024-10-31 (fresh 10,000 USDT, baseline costs, 1x).
time_in_market = share of DEV rebalance bars with s > 0. Eligible if time_in_market >= 50%.
Score = DEV CAGR (from DEV net and DEV length) / DEV max drawdown. Winner = eligible candidate with the highest score.
The always-on book's DEV score is reported as the reference. If the winner does not beat the always-on DEV score,
that is reported plainly ("no gate helped even on DEV"), and the winner is still carried through TEST and the checks.

## TEST (once) and multiple-testing haircut
After the winner is fixed, run TEST 2024-11-01..2026-10-21 for ALL 6 candidates and the always-on book (baseline and
doubled costs) and report all of them. Haircut: the winner's full-period (2021-01..2026-10, 70 months) t-stat of the
monthly mean must be >= 2.0 x sqrt(ln(6) + 1) = 3.34, otherwise it is reported as missing the haircut.

## The 8 ROBUSTNESS.md checks on the gated winner (1x, full 2021-01..2026-10), as in r2 with these additions
1-4, 7, 8: exactly as r2_rotation/PREREG.md, applied to the gated book (gate signals recomputed on the reduced
universe for check 7, since the gate reads the universe).
5. Neighbours, one thing moved at a time, PASS if >= 70% are net > 0 on DEV AND TEST at baseline costs:
   (a) gate threshold neighbours: for DISP/BTCVOL the median multiplier {0.5, 0.75, 1.25, 1.5};
       for MOM8 the threshold {-5%, -2.5%, +2.5%, +5%} (a +-25/50% move of 0 is undefined, so absolute steps);
       for BTC100 the band price/SMA - 1 > {-5%, -2.5%, +2.5%, +5%}; for BREADTH the band half-width {0.05, 0.075, 0.125, 0.15}
       around 0.5; for RANKCORR the threshold {-0.10, -0.05, +0.05, +0.10}.
   (b) gate lookback neighbours, main lookback x {0.5, 0.75, 1.25, 1.5} (rounded to whole days / observations):
       DISP median window 365 -> {183, 274, 456, 548}; MOM8 56 -> {28, 42, 70, 84}; BTC100 SMA -> {50, 75, 125, 150};
       BTCVOL vol window 30 -> {15, 22, 38, 45}; BREADTH SMA 50 -> {25, 38, 63, 75}; RANKCORR averaged obs 4 -> {2, 3, 5, 6}.
   (c) the 18 base-strategy neighbours of r2 (lookback {7,10,18,21,28}, k {5,8,12,15}, rebalance {1,3,5,10,14},
       universe {20,30,50,60}) with the winner's gate unchanged (for MOM8 the always-on reference book uses the neighbour's
       own base parameters).
   26 neighbours in total; PASS if >= 19 are positive on both halves.
6. Beats chance, two tests, BOTH must be >= 95%:
   (a) 500 random gates: the winner's ON/OFF values over the full run's rebalance bars shuffled at random (same
       time in market, same base picks); (b) 500 random coin-pick books (10 random longs / 10 random shorts from the
       same eligible universe) run with the winner's actual gate. The gated book's full-run net must beat >= 95% of each.

## Also reported
The winner's 2026 by calendar day (each daily bar assigned to the day it covers, as r2_rotation/year2026.py), months,
weekly up/down counts, time in market, and the always-on max drawdown vs the gated one (full run and 2026).
