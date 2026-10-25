"""DEV-only sweep (2021-01..2024-10). python dev_sweep.py [interval]  -> data/monthly20_02_xs_momentum/dev_sweep_<iv>.json"""
import json, sys, time, itertools
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/02_xs_momentum")
from engine import *
from signals import *

IV = sys.argv[1] if len(sys.argv) > 1 else "1d"
UNIV_N = 40


def build(iv, univ_n=UNIV_N):
    syms = [s["symbol"] for s in all_symbols()]
    D = Panel("1d", syms)
    um_day = universe_mask(D, univ_n)
    ever = np.where(um_day.any(0))[0]
    keep = [syms[j] for j in ever]
    if "BTCUSDT" not in keep:
        keep.append("BTCUSDT")
    D = Panel("1d", keep); um_day = universe_mask(D, univ_n)
    # NOTE: the universe is ranked among ALL perps; restricting to `keep` is exact because
    # every coin outside `keep` was never top-n on any day.
    if iv == "1d":
        P, um = D, um_day
    else:
        P = Panel(iv, keep)
        day_idx = ((P.t - DATA_FROM) // DAY).astype(int)
        um = um_day[day_idx]
    P.load_funding()
    return P, um, keep


def scores(P, iv):
    per_day = DAY // INTERVAL_MS[iv]
    btc = P.syms.index("BTCUSDT")
    S = {}
    for Ld in ([1, 3, 7, 14, 28] if iv == "1d" else [1 / 6, 0.5, 1, 3, 7]):
        L = max(1, int(round(Ld * per_day)))
        S[f"ret{L}"] = ret_lb(P, L)
    for Ld in [3, 7, 28]:
        L = int(Ld * per_day)
        S[f"resid{L}"] = resid_btc(P, L, 60 * per_day, btc)
    for Ld in [1, 3]:
        L = int(Ld * per_day)
        S[f"vsurge{L}"] = vol_surge(P, L, 30 * per_day)
    return S


if __name__ == "__main__":
    t0 = time.time()
    P, um, keep = build(IV)
    print("coins", len(keep), "missing cache", sum(np.isnan(P.c).all(0)), time.time() - t0, flush=True)
    S = scores(P, IV)
    per_day = DAY // INTERVAL_MS[IV]
    legs = [(1, 0), (3, 0), (5, 0), (0, 1), (0, 3), (0, 5), (1, 1), (3, 3), (5, 5), (10, 10)]
    rebals = [1, 7] if IV == "1d" else [1, 6]
    res = []
    for (name, sc), mode, (kl, ks), rb in itertools.product(S.items(), ["mom", "rev"], legs, rebals):
        p = dict(k_long=kl, k_short=ks, rebal=rb, lev=1.0, mode=mode)
        r = run(P, um, sc, p, DEV_START, DEV_END)
        r2 = run(P, um, sc, p, DEV_START, DEV_END, cost_mult=2.0) if r["mean_month"] > 2 else {"mean_month": float("nan")}
        row = dict(signal=name, **p, mean=r["mean_month"], median=r["median_month"], geo=r["geo_month"],
                   worst=r["worst_month"], dd=r["max_dd_pct"], net=r["net_pct"], mean2x=r2["mean_month"],
                   trades=r["trades"], top3=r["top3_share_of_profit"], months=r["months"])
        res.append(row)
        print(f"{name:9s} {mode} L{kl} S{ks} rb{rb}  mean {r['mean_month']:7.2f} med {r['median_month']:7.2f} geo {r['geo_month']:6.2f} "
              f"dd {r['max_dd_pct']:6.1f} 2x {r2['mean_month']:7.2f}", flush=True)
    MY_DATA.mkdir(parents=True, exist_ok=True)
    (MY_DATA / f"dev_sweep_{IV}.json").write_text(json.dumps(res, default=float))
    print("done", time.time() - t0)
