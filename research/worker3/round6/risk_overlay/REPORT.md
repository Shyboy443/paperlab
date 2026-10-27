# Worker 3 round 6: risk_overlay — REPORT (2026-10-27)

**Verdict: FAIL** (neither IMPROVED nor BETTER-RISK).

## What was done
- Copied `lib5.py` to `lib6.py` and added risk overlays, all off by default. With every overlay off it reproduces the
  base rule exactly: DEV +5.13 / TEST +10.33 / HOLDOUT +5.18, DD 26.27 / 39.94 / 28.51 (`verify.py`).
- Tried 25 configs on DEV only (`dev.py`, `dev.log`, `devtable.md`) across four families: book vol targeting,
  inverse-vol weights, short-leg protection (stop / age filter / vol filter / smaller short leg) and a drawdown brake,
  plus combinations. The selection rule was fixed in advance; see `PREREG.md`.
- **Selected:** base + `vt=60` (60-day book vol targeting, scale-down only, target = the expanding median of its own vol)
  + `sstop=0.15` (a 15% intraday stop on each short, measured from the day's open).
- `final.py` ran TEST once and HOLDOUT once (`final.log`, `results.json`).

## Results (1x, all costs)
| Window | Rule | Geo month | Median | Months up | Max DD | Liquidations | Net 2x costs |
|---|---|---|---|---|---|---|---|
| DEV | current | +5.13 | +2.78 | 27/46 | 26.3% | 3 | +521% |
| DEV | selected | +5.67 | +3.05 | 28/46 | 21.1% | 0 | +677% |
| TEST | current | +10.33 | +10.23 | 16/24 | 39.9% | 9 | +738% |
| TEST | selected | **+8.02** (78%) | +7.95 | 15/24 | **25.9%** (-14.1) | 0 | +408% |
| HOLDOUT | current | +5.18 | +3.25 | 24/36 | 28.5% | 1 | +360% |
| HOLDOUT | selected | **+4.43** (86%) | +3.55 | 22/36 | **22.8%** (-5.7) | 1 | +269% |

Checks: TEST 7/8 (still fails check 7: the top coins are MYX, PIPPIN and BEAT, and the largest position share is
11.65%), HOLDOUT 8/8.

How it measures against the bar:
- **IMPROVED:** fails. Geo month is below 90% of the current rule on both TEST (8.02 < 9.3) and HOLDOUT (4.43 < 4.66).
- **BETTER-RISK:** fails. TEST DD is 14.1 points lower, but HOLDOUT DD is only 5.7 points lower (10 needed). Geo stays
  at or above 75% of the current rule on both windows.
- **Doubled costs:** positive on both windows.

## Key findings
1. **The short stop's DEV gain comes from the fill assumption.**
   - With one extra 1% adverse slippage on stop fills, DEV geo falls to +4.92, below the base. With 2%, it falls to
     +4.18.
   - On TEST the stop lowers geo, and it stays below the base at every stop level tried: geo +8.3 to +9.5 for stops of
     0.10 to 0.40.
   - It does remove the liquidations (TEST 9 -> 0), but each stop also cuts a short that would have come back.
2. **Vol targeting is where the drawdown cut comes from.**
   - TEST without it (sstop only): DD 33.2%, geo +9.12.
   - HOLDOUT without it: DD 33.2%, worse than the base.
   - It is cheap on DEV (average scale 0.985) but costly on TEST (0.853) and HOLDOUT (0.87). Those windows have more
     high-vol spells, so geo drops.
3. **The other overlays all lost on DEV.**
   - Inverse-vol weights: geo +3.7.
   - Short-age filter: higher geo, but DD rose to 30-32%.
   - Short-vol filter and smaller short leg: both worse.
   - Drawdown brake: geo -0.3 to -0.7.
4. **Neighbours (reported, not used):** `vt=20` + `sstop=0.15` gives TEST +8.20 with DD 25.8%, and HOLDOUT +4.79 with
   DD 21.1%. That is still short of both bars on HOLDOUT (DD -7.4).

**Conclusion:** keep the current rule. These risk overlays trade return for drawdown roughly one-for-one. None clears
the brief's bar out of sample, and the stop's benefit depends on optimistic fills.
