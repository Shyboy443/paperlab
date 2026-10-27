# Worker 3 round 6 - execution: FAIL (but a 60% order cut at unchanged risk)

Files: `lib6.py` (lib5 + OFF-by-default band/buf/minn/binance options; bit-identical default verified by `verify.py`:
DEV +5.13 / TEST +10.33 / HOLDOUT +5.18), `PREREG.md` (grid, rule, addendum written before TEST/HOLDOUT),
`dev.py` / `dev.log` / `dev.json` / `devtable.md` (21 configs), `final.py` / `final.log` / `results.json` (single TEST+HOLDOUT run).

## DEV grid (21 configs, all in devtable.md)
- No-trade band 0.10 / 0.25 / 0.50: DEV geo +5.14 / +5.31 / +5.52 (base +5.13); orders/month 370 -> 201 / 163 / 154.
  Turnover only falls 14.8x -> 13.7x/month: almost all turnover is entries/exits, the re-sizes are many but tiny.
- Rank buffer (keep while rank <= 6/7/8, daily equal-weight): clearly worse (+4.38 / +3.15 / +2.33, DD up to 40%).
  Ranks 6-8 carry no edge; the signal wants the strict top 5. every=2: +3.52 (as round5's neighbours said).
- Order floors (min 5/10 USDT, Binance 5 USDT / ETH 20 / BTC 100) at 500 and 2,000 USDT start: harmless
  (DEV geo +5.06..+5.13). At 500 USDT, ~74 BTC/ETH entries are skipped (slot stays cash); fades as equity grows.
- Selected by the pre-registered rule: **base + band 0.50** (best DEV geo inside the DD/concentration filters).

## Selected vs current rule (1x, all costs)
| Window | Rule | Median | Geo | Up | Max DD | Net 2x cost | Orders/mo |
|---|---|---|---|---|---|---|---|
| DEV | current | +2.78 | +5.13 | 27/46 | 26.3% | +521% | 371 |
| DEV | band 0.5 | +3.58 | +5.52 | 29/46 | 26.9% | +661% | 154 |
| TEST | current | +10.23 | +10.33 | 16/24 | 39.9% | +738% | 361 |
| TEST | band 0.5 | +8.51 | **+9.28** | 14/24 | 39.1% | +585% | 139 |
| HOLDOUT | current | +3.25 | +5.18 | 24/36 | 28.5% | +360% | 307 |
| HOLDOUT | band 0.5 | +4.06 | +5.22 | 24/36 | 24.6% | +370% | 120 |

Checks: TEST 7/8 (check 7 fails, largest position 16.0% of profit, was 15.3%); HOLDOUT 8/8. Neighbour fractions 1.0 / 1.0.
Short liquidations unchanged (TEST 9, HOLDOUT 1). Doubled costs positive on both windows.

## Verdict: FAIL
- TEST geo +9.28 < +9.30 bar (by 0.02), and none of DD -5 / +2 months up / higher geo holds on TEST (DD only -0.8, 2 fewer months up).
- HOLDOUT alone would pass (geo up, DD -3.9 but not -5 -> passes via higher geo).
- Not BETTER-RISK (no 10-point DD cut).

## Notes (post hoc, NOT selectable)
- Exec neighbours: band 0.25 on TEST +9.71 geo, 18/24 up; HOLDOUT +5.35, DD 24.7%. Band 1.0: TEST +8.75, DD 35.7%.
  All bands are within noise of the base: the band is a cost/ops change, not an edge change.
- Live realism (band 0.5 + Binance floors): 500 USDT start TEST +9.37 / HOLDOUT +5.08; 2,000 USDT +9.28 / +5.24.
  Current rule + Binance floors at 500 USDT: TEST +10.27 / HOLDOUT +5.10 (so 500 USDT is workable as is).
- Practical takeaway for the live bot: a 25-50% no-trade band cuts orders/month by ~60% (fewer API calls, fewer dust
  orders under 5 USDT that Binance would reject) for roughly the same return; it is not a performance upgrade.
