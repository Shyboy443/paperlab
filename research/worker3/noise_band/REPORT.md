# Noise band on crypto: FAIL (2026-10-27)

The rule, data and pass bar are in PREREG.md. The code is run.py: 6 perps on 15m bars, the US session 13:30-20:00 UTC,
at most 1x, with 0.07% cost per side. These are monthly % returns.

| Run | Window | Median month | Geo month | Months up | Max DD |
|---|---|---|---|---|---|
| Primary | DEV 2021-22 | −1.87 | −1.74 | 8/24 | 38% |
| Primary | DEV 2023-24 | −3.61 | −2.88 | 4/22 | 48% |
| Primary | **TEST 2024-11..2026-10** | **−3.06** | **−2.16** | **7/24** | **41%** |
| Start 14:30 | TEST | −1.61 | −1.44 | 10/24 | 30% |
| 2x costs | TEST | −6.91 | −5.51 | 5/24 | 74% |
| No costs | TEST | +0.51 | +1.30 | 14/24 | 12% |

- **Gross edge:** there is a small one, about +1% a month at zero cost. It is far below Worker 3's +10%.
- **Costs:** the rule re-enters many times a session, so trading costs more than wipe the edge out.
- **Verdict:** it fails every period, so the luck checks were not run. It is not a Worker 3 candidate and not an
  extra leg.
- **Past results:** this agrees with monthly20/06_intraday_breakout (US-open ORB, FAIL). The reel's claim covers
  NASDAQ/S&P futures, where trading costs much less per trade. Nothing here tests that claim.
