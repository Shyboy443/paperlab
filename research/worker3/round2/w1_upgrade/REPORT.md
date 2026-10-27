# w1_upgrade (Worker 3 round 2): FAIL — no pre-registered upgrade beats Worker 1 on TEST

The agent could not write this file, so it was saved from its final message. The Worker 1 baseline reproduces exactly
(TEST median +3.84%, geo +5.33%).

## Variants
All are at ≤ 1x gross:
- **V1:** vol-scaled legs (Barroso & Santa-Clara 2015; Grobys et al. 2025).
- **V2:** momentum crash protection (Daniel & Moskowitz 2016).
- **V3:** inverse-variance leg weights.
- **V4:** Worker 2 as a 4th leg, using the gross room W1 leaves inside one account at a joint 1x cap.

## DEV selection
The rule was pre-registered. V3 and V4 qualified on DEV.

**Pre-registered for TEST:** PRIMARY V4, SECONDARY V4+V3.

## Results at 1x
Each cell reads median / geo / mean month (%), months up, max DD.

| | DEV | TEST | Full |
|---|---|---|---|
| W1 | 1.87 / 2.96 / 3.16, 29/46, 20.2% | 3.84 / 5.33 / 5.86, 17/24, 23.3% | 2.73 / 3.77 / 4.09, 46/70 |
| V4 | 5.69 / 5.56 / 5.87, 33/46, 16.8% | 4.38 / 5.09 / 5.67, 14/24, 19.0% | 5.38 / 5.40 / 5.80, 47/70 |
| V4+V3 | 5.03 / 5.58 / 5.86, 35/46, 17.9% | 5.81 / 4.65 / 5.26, 13/24, 19.3% | 5.38 / 5.26 / 5.65, 48/70 |

## Checks
- V4: 7/8. Check 7 fails narrowly: one EVAA trade is 10.19% of profit.
- V4+V3: 8/8.

## Correlation
Monthly correlation with W1 is 0.93–0.95, so these variants add nothing to a W1+W2 portfolio.

## Why V4 failed
The headroom scale factor is small exactly on W2's many-signal days, which are its best days. So it shrinks W2's winners:
W2 trades summed to +0.80 standalone, but −0.03 inside the headroom.

The 50/50 two-account mix gives W2 a fixed sleeve, so it doesn't have this problem.

## Unselected variants on TEST (info only)
- V2 crash protection: 18/24 months up, median +4.51%, geo +5.19%. It failed its DEV qualification, so it would need a
  forward test.

## Verdict
**FAIL**:
- (b) TEST geometric month is below +5.33%;
- (c) 13–14/24 months up.
