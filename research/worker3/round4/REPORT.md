# Worker 3 round 4 (2026-10-26): holdout validation on 2018–2020 (unseen)

| Lead | Holdout verdict | Key numbers (holdout, 1x) |
|---|---|---|
| high52 ORIG (Donchian-30 L/S) | **YES** (8/8 checks) | geo +3.50%, t 2.75, +245% net (+177% at 2x), DD 25.4%; effectively 23 active months |
| W1 + crash protection (V2) | NO | loses to plain W1 (geo 2.58 vs 2.69) |
| hks 12h periodicity | PARTLY, so drop | net geo +0.65%, 1/8 checks; real before costs (gross +4.2%), killed by about 460 trades a month |

## Confirmed on unseen data
- **Plain W1** passes all applicable checks.
- **W1 + W2 50/50** beats either alone (geo +3.30%, 26/36 months up, month-end DD 11.5%, t 4.3).
- **W1 + W2 + high52 in thirds** gives geo +3.43%, the same 26/36 months up, and a lower monthly DD (7.8%).

## Conclusion
No single strategy beats Worker 1's +5.33% TEST bar.

The real Worker 3 is **high52 ORIG as a third leg**. It is now validated on 3 separate periods (DEV, TEST and the holdout)
and passes 8/8 checks on the holdout. Over 2021–26 the three-way mix is about equal to 50/50, so the gain is mainly lower
drawdown on the holdout.

**Next step:** a forward paper test (no money).
