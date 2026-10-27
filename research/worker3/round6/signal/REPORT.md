# Round 6, signal: FAIL (saved from the agent's final message)

**Selected: C1 "s7neg".** This is the built rule, but a coin is shorted only if it is at its 30-day low AND its 7-day
return is negative. It was the only one of 15 DEV configs that improved on the base rule (PREREG.md).

| Window | Rule | Median | Geo | Up | Max DD | Net, 2x cost | Short liqs |
|---|---|---|---|---|---|---|---|
| DEV | current | +2.78 | +5.13 | 27/46 | 26.3% | +521% | 3 |
| DEV | C1 | +3.04 | +5.75 | 26/46 | 27.3% | +674% | 2 |
| TEST | current | +10.23 | +10.33 | 16/24 | 39.9% | +738% | 9 |
| TEST | C1 | +9.21 | +9.96 | 16/24 | 38.8% | +653% | 6 |
| HOLDOUT | current | +3.25 | +5.18 | 24/36 | 28.5% | +360% | - |
| HOLDOUT | C1 | +6.40 | +4.64 | 21/36 | 34.9% | +267% | 1 |

**Checks:**
- TEST 7/8. Check 7 fails: RAVE is 13.9% of profit.
- HOLDOUT 8/8.

**Why it is FAIL:**
- **TEST:** geo is above the 90% floor, but there is no DD, months-up or geo gain.
- **HOLDOUT:** geo +4.64 is below the +4.66 floor, DD is 6.4 points worse, and there are 3 fewer months up.
- **Possible cause:** on 28% of DEV days fewer than 5 coins qualified for C1's short leg. The DEV gain may come from
  that partial net-long tilt.

**DEV grid results (all worse or not enough):**
- **BTC-relative and residual channel:** geo 3.5–4.7, DD up to 55%.
- **Vol-scaled position:** geo +5.21, DD 32%.
- **One-day-jump filter at 40/50/60%:** at most +5.31. It does not reduce concentration.
- **Short leg on a 60- or 90-day channel:** worse or flat.
- **Minimum listing age 60/90/180 days:** geo 4.55 / 4.32 / 3.38.

**Learned:** the edge is the raw channel position, which moves with the market. Anti-lottery and listing-age filters
only lower the return.

**Engine check:** lib6.py reproduces the base rule exactly (DEV +5.13 / TEST +10.33 / HOLDOUT +5.18).
