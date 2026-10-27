# Worker 3 round 6 - execution (pre-registration, written BEFORE any DEV run of a variant)

Base = round5 high52_plus selected cfg {book LS, exit D, chan 30, univ 20, k 5} (lib5). `lib6.py` = lib5 copy with
OFF-by-default options; verified bit-identical: DEV geo +5.13 / TEST +10.33 / HOLDOUT +5.18 (verify.py).
Base trades ~370 orders/month on DEV; 57% of them are smaller than 1% of equity (= 5 USDT at 500 USDT).

## Options (mechanical priors: fewer orders -> less fee+slippage; no new signal)
- `band`  relative no-trade band on held same-side positions (entries/exits always trade; a held position above the
  1% volume cap is always cut; if gross would exceed 1x the most overweight holds are forced back to target).
- `buf`   rank buffer, k=5: keep a holding while its leg rank <= buf, re-sized daily to equal weight, free slots filled
  from the top. NOT round5's hysteresis (that was rank <= 2.0*k with drifting sizes, k=10/univ 40, rows 3/4/9...).
- `every` rebalance every n days (round5 neighbours already showed every=2,3,5,7 all worse on DEV; only every=2 is re-logged).
- `minn`  minimum order notional (USDT); `binance` per-symbol USDM floor 5 USDT, ETH 20, BTC 100. Orders below the
  floor are skipped (entries -> slot stays cash; re-sizes -> keep current size); exits always trade.
  These depend on the starting equity (500 / 2,000 USDT) and are REALISM CHECKS, not selectable signal changes.

## DEV grid (all logged in dev.log / devtable.md)
1 base | 2-4 band 0.10/0.25/0.50 | 5-7 buf 6/7/8 | 8 every 2 | 9 band .25 + buf 7 | 10 band .10 + buf 7
11-12 minn 5 / 10 @500 | 13 minn 10 @2000 | 14-16 binance @500 / @2000 / @10k
17 binance+band .25 @500 | 18 binance+buf 7 @500 | 19 binance+band .25+buf 7 @500 | 20 binance+band .25 @2000
21 binance+band .25+buf 7 @2000

## Selection rule (DEV only)
Selectable = configs 1-10 (equity 10k, no order floor; comparable with the base numbers).
Filters: DEV max DD <= 28.3% and largest position <= 15% of net.
Pick the highest DEV geo; within 0.10 geo of the best, prefer the lower turnover. If nothing beats the base geo by
> 0.10 but a config is within 0.10 geo with DD >= 3 points lower, pick it. Otherwise the selected config is the best by
geo anyway (likely FAIL) and still gets the single TEST/HOLDOUT run.
The selected config is then also reported with `binance=True` at 500 and 2,000 USDT start (live realism).

## Neighbour grid of the selected config
final.py NB (lscale 0.5/0.75/1.25/1.5, k 3/4/6/8, every 2/3/5/7, univ 10/15/25/30) applied on top of the selected
execution options, plus its own execution parameter neighbours (band x0.5/x2, buf +-1), shown separately.

## Addendum (written after DEV, BEFORE the single TEST and HOLDOUT run)
DEV results: devtable.md. Filters passed by 1-5 (buf 7/8, every 2 and the buf combos fail DD <= 28.3 or top pos <= 15).
Best DEV geo = band .50 (+5.52, DD 26.9%, top pos 12.83%); band .25 (+5.31) is not within 0.10, so the turnover
tie-break does not apply. **SELECTED: base + band=0.50** (no-trade band of 50% relative drift; entries/exits daily).
Note: on DEV it meets the IMPROVED DEV bar only on geo (+5.52 vs +5.13), DD is 0.6 pt higher.
Neighbours: final.py NB on top of band=0.5, plus band 0.25 / 1.0 (exec neighbours, reported separately).
Live realism (reported, not selection): band 0.5 + binance floors at 500 and 2,000 USDT start, on TEST and HOLDOUT.
