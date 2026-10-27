# PREREG: Worker 3 round 6, risk_overlay (written 2026-10-27, BEFORE any TEST/HOLDOUT run of an overlay)

Only the base rule was run on TEST/HOLDOUT (`verify.py`), to prove `lib6.py` with every overlay off is bit-identical to
`round5/high52_plus/lib5.py`: DEV geo +5.13 / TEST +10.33 / HOLDOUT +5.18 reproduced exactly (DD 26.27 / 39.94 / 28.51;
liquidations 3 / 9 / 1). Those numbers were already public in the brief.

## Overlays (lib6.py, all off by default)
- `vt=W`: book vol targeting, scale-down only (Barroso & Santa-Clara 2015; Daniel & Moskowitz 2016). Daily unscaled book
  return series; at each open, sigma = stdev of the last W daily returns; target = expanding median of that sigma series
  (no fitted vol level); gross x min(1, target/sigma). Scale = 1 until W days of the run exist.
- `ivw=1`: inverse 20-day-vol weights inside each leg (same leg totals).
- `sstop=X`: a short is covered intraday when the high reaches entry (= that day's open) x (1+X), filled at
  max(level, open) plus the normal cost; re-entry allowed at the next open if still in the bottom 5. Mechanical prior:
  the 1x liquidation loses 100% of a 0.1-equity position; a stop caps it at X.
- `sage=N`: coins with fewer than N days of data excluded from the short leg only.
- `svol=m`: short candidates with 20d vol > m x the universe median (or no 20d vol yet) excluded.
- `sw=x`: short leg budget x (long stays 0.5).
- `ddb/ddr`: drawdown brake: gross x0.5 once book DD >= ddb, restored when DD <= ddr.
- `sslip`: extra adverse slippage on stop fills (robustness only, not a selection variable).

One bug fixed during DEV: `ivw` produced inf weights for zero-vol coins (first DEV run logged in `dev_run1_ivwbug.log`);
fixed to fall back to the median weight, DEV grid re-run. Selection identical in both runs.

## Every config tried (DEV 2021-01..2024-10 only, 1x, all costs) — 25 configs + base
Stage 1: each family alone. Family winner = max DEV geo among the family's configs with DD <= base DD (else max geo).
Stage 2: all pairs of family winners, and the triples that contain C_short. (Winners: vt=60, ivw=1, sstop=0.15,
ddb=0.25/ddr=0.125.)

| # | family | overlay | DEV geo | median | up | max DD | net % | net 2x % | liq | stops | avg scale |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | BASE | `{}` | +5.13 | +2.78 | 27/46 | 26.3 | 899 | 521 | 3 | 0 | 1.0 |
| 1 | A_voltarget | `{"vt": 20}` | +5.16 | +3.21 | 29/46 | 24.6 | 910 | 540 | 3 | 0 | 0.956 |
| 2 | A_voltarget | `{"vt": 60}` | +5.17 | +2.79 | 27/46 | 25.8 | 918 | 537 | 3 | 0 | 0.988 |
| 3 | A_voltarget | `{"vt": 126}` | +5.11 | +2.78 | 27/46 | 26.3 | 892 | 518 | 3 | 0 | 0.997 |
| 4 | B_invvol | `{"ivw": 1}` | +3.69 | +1.96 | 29/46 | 27.5 | 430 | 214 | 3 | 0 | 1.0 |
| 5 | C_short | `{"sstop": 0.15}` | +5.64 | +3.05 | 28/46 | 22.4 | 1146 | 660 | 0 | 288 | 1.0 |
| 6 | C_short | `{"sstop": 0.25}` | +5.16 | +3.03 | 29/46 | 26.9 | 910 | 525 | 0 | 88 | 1.0 |
| 7 | C_short | `{"sstop": 0.4}` | +5.36 | +2.78 | 28/46 | 26.3 | 1004 | 586 | 0 | 18 | 1.0 |
| 8 | C_short | `{"sstop": 0.6}` | +5.31 | +2.78 | 27/46 | 26.3 | 979 | 571 | 0 | 5 | 1.0 |
| 9 | C_short | `{"sage": 90}` | +5.77 | +3.50 | 28/46 | 29.9 | 1222 | 736 | 3 | 0 | 1.0 |
| 10 | C_short | `{"sage": 180}` | +5.53 | +2.95 | 30/46 | 32.3 | 1091 | 665 | 3 | 0 | 1.0 |
| 11 | C_short | `{"svol": 2.0}` | +4.73 | +1.55 | 27/46 | 31.6 | 737 | 422 | 2 | 0 | 1.0 |
| 12 | C_short | `{"sw": 0.35}` | +5.00 | +2.18 | 28/46 | 29.3 | 842 | 528 | 3 | 0 | 1.0 |
| 13 | D_ddbrake | `{"ddb": 0.15, "ddr": 0.075}` | +4.39 | +1.98 | 27/46 | 23.5 | 623 | 368 | 3 | 0 | 0.773 |
| 14 | D_ddbrake | `{"ddb": 0.2, "ddr": 0.1}` | +4.71 | +1.92 | 27/46 | 23.6 | 732 | 425 | 3 | 0 | 0.819 |
| 15 | D_ddbrake | `{"ddb": 0.25, "ddr": 0.125}` | +4.87 | +2.78 | 27/46 | 26.2 | 791 | 390 | 3 | 0 | 0.975 |
| 16 | A_voltarget+B_invvol | `{"vt": 60, "ivw": 1}` | +3.72 | +1.95 | 29/46 | 26.0 | 436 | 222 | 3 | 0 | 0.968 |
| 17 | A_voltarget+C_short | `{"vt": 60, "sstop": 0.15}` | +5.67 | +3.05 | 28/46 | 21.1 | 1163 | 677 | 0 | 288 | 0.985 |
| 18 | A_voltarget+D_ddbrake | `{"vt": 60, "ddb": 0.25, "ddr": 0.125}` | +4.92 | +2.79 | 27/46 | 25.7 | 812 | 409 | 3 | 0 | 0.964 |
| 19 | B_invvol+C_short | `{"ivw": 1, "sstop": 0.15}` | +4.60 | +1.95 | 30/46 | 18.2 | 691 | 361 | 0 | 288 | 1.0 |
| 20 | B_invvol+D_ddbrake | `{"ivw": 1, "ddb": 0.25, "ddr": 0.125}` | +3.38 | +1.32 | 28/46 | 25.8 | 362 | 160 | 3 | 0 | 0.829 |
| 21 | C_short+D_ddbrake | `{"sstop": 0.15, "ddb": 0.25, "ddr": 0.125}` | +5.64 | +3.05 | 28/46 | 22.4 | 1146 | 558 | 0 | 288 | 1.0 |
| 22 | A_voltarget+B_invvol+C_short | `{"vt": 60, "ivw": 1, "sstop": 0.15}` | +4.63 | +1.97 | 31/46 | 16.3 | 704 | 373 | 0 | 288 | 0.983 |
| 23 | A_voltarget+C_short+D_ddbrake | `{"vt": 60, "sstop": 0.15, "ddb": 0.25, "ddr": 0.125}` | +5.67 | +3.05 | 28/46 | 21.1 | 1163 | 576 | 0 | 288 | 0.985 |
| 24 | B_invvol+C_short+D_ddbrake | `{"ivw": 1, "sstop": 0.15, "ddb": 0.25, "ddr": 0.125}` | +4.60 | +1.95 | 30/46 | 18.2 | 691 | 361 | 0 | 288 | 1.0 |

## Selection rule (fixed before looking at the table; applied mechanically in dev.py)
Among configs with DEV doubled-cost net > 0:
1. max DEV geo among configs with geo > base (5.13) and DD <= base DD + 1;
2. else min DD among geo >= 0.97 x base and DD <= base DD - 3;
3. else max geo/DD among geo >= 0.75 x base.

Rule 1 applied.

## THE ONE selected variant
`{"book":"LS","exit":"D","chan":"30","univ":20,"k":5,"vt":60,"sstop":0.15}`
= the Worker 3 rule + 60-day book vol targeting (scale-down only) + a 15% intraday stop on each short.
DEV: geo +5.67 (base +5.13), median +3.05, 28/46 up, max DD 21.1% (base 26.3%), net 2x +677%, 0 liquidations, 288 stops.

Honest caveats known before TEST: sstop=0.15 is the edge of the stop grid and the stop curve is not monotone
(0.15: +5.64, 0.25: +5.16, 0.40: +5.36, 0.60: +5.31 on DEV); vt=60 changes almost nothing (avg scale 0.985).
Daily bars: the stop fill at the exact level is optimistic in a fast spike, hence the stop-slippage runs below.

## Neighbour grid (run once with TEST and HOLDOUT)
- Check 5 grid (same as round 5, applied on top of the overlay): lscale 0.5/0.75/1.25/1.5; k 3/4/6/8; every 2/3/5/7;
  univ 10/15/25/30.
- Overlay neighbours: sstop 0.10 / 0.20 / 0.25 / 0.40 (vt=60 kept); vt 0 / 20 / 126 (sstop=0.15 kept).
- Stop-slippage robustness: sslip 0.01 and 0.02.
- Doubled costs on every window.

## Then: TEST once (2024-11..2026-10) and HOLDOUT once (2018–2020) with final.py; verdict by the brief's bar.
