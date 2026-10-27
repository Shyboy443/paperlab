"""DEV-only grid (2021-01..2024-10). TEST is NOT touched here."""
import json, sys, time, itertools
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round2/venue_premium")
from lib import *  # noqa

t0 = time.time()
S = build_signals(force="force" in sys.argv)
KP, EP, CBP, CBPE = S["KP"], S["EP"], S["CBP"], S["CBPE"]
i_dev = (P.t >= DEV_START) & (P.t < DEV_END)
print("coverage DEV: KP", np.isfinite(KP[i_dev]).mean().round(3), "CBP", np.isfinite(CBP[i_dev]).mean().round(3),
      "coins with EP", int(S["has_up"].sum()), flush=True)
print("KP DEV pct 5/50/95", np.nanpercentile(KP[i_dev], [5, 50, 95]).round(4), "CBP", np.nanpercentile(CBP[i_dev], [5, 50, 95]).round(5))
res = {"timing": [], "xs": []}


def st(r):
    return dict(net=r["net_pct"], geo=r["geo_month"], med=r["median_month"], mean=r["mean_month"], up=r["months_up"],
                dd=r["max_dd_pct"], tr=r.get("trades"))


for name, a in (("BTC", [BTC]), ("BTCETH", [BTC, ETH])):
    res["timing"].append(dict(sig="buyhold", assets=name, **st(run_timing(np.ones(T), a, DEV_START, DEV_END))))
    for Sd in (1, 3, 7, 14):
        s = rmean(CBP, Sd)
        for mode in ("LF", "LS"):
            pos = np.where(np.isnan(s), 0, np.where(s > 0, 1, 0 if mode == "LF" else -1))
            res["timing"].append(dict(sig="CBP>0", S=Sd, mode=mode, assets=name, **st(run_timing(shift(pos.astype(float)), a, DEV_START, DEV_END))))
        for L in (30, 90):
            z = (s - rmean(CBP, L)) / rstd(CBP, L)
            pos = np.where(np.isnan(z), 0, np.where(z > 0, 1, 0))
            res["timing"].append(dict(sig="CBPz>0", S=Sd, L=L, mode="LF", assets=name, **st(run_timing(shift(pos.astype(float)), a, DEV_START, DEV_END))))
    for Sd, L, thr in itertools.product((1, 7), (90, 180), (0.0, 1.0, 1.5)):
        z = (rmean(KP, Sd) - rmean(KP, L)) / rstd(KP, L)
        for mode in ("LF", "LS"):
            pos = np.where(np.isnan(z), 0, np.where(z < thr, 1, 0 if mode == "LF" else -1))
            res["timing"].append(dict(sig="KPz<thr", S=Sd, L=L, thr=thr, mode=mode, assets=name, **st(run_timing(shift(pos.astype(float)), a, DEV_START, DEV_END))))
    for x in (0.0, 0.02, 0.04, 0.06):
        s = rmean(KP, 3)
        pos = np.where(np.isnan(s), 0, np.where(s < x, 1, 0))
        res["timing"].append(dict(sig="KP<x", x=x, S=3, mode="LF", assets=name, **st(run_timing(shift(pos.astype(float)), a, DEV_START, DEV_END))))
print("timing done", time.time() - t0, flush=True)

UM = {}
for kind, Sd, sign, k, legs, rb, univ in itertools.product(("lvl", "chg"), (1, 3, 7, 14), (-1, 1), (3, 5), ("LS", "L", "S"), (1, 3, 7), (40, 80)):
    if univ not in UM:
        UM[univ] = E.universe_mask(P, univ)
    sig = rmean(EP, Sd) if kind == "lvl" else rmean(EP, Sd) - rmean(EP, 30)
    sc = shift(sign * sig)
    kL = k if legs in ("LS", "L") else 0; kS = k if legs in ("LS", "S") else 0
    r = E.run(P, UM[univ], sc, dict(k_long=kL, k_short=kS, rebal=rb, lev=1.0, mode="mom"), DEV_START, DEV_END)
    res["xs"].append(dict(kind=kind, S=Sd, sign=sign, k=k, legs=legs, rebal=rb, univ=univ, **st(r)))
print("xs done", time.time() - t0, flush=True)
json.dump(res, open(r"D:/Projects/BinanceBOt/paperlab/research/worker3/round2/venue_premium/dev_grid.json", "w"), indent=0, default=float)
for key in ("timing", "xs"):
    rows = sorted(res[key], key=lambda x: -x["geo"])
    print("==", key, "top 15 by DEV geo")
    for x in rows[:15]:
        print(x)
# fraction positive by family
xs = res["xs"]
for kind in ("lvl", "chg"):
    for sign in (-1, 1):
        for legs in ("LS", "L", "S"):
            g = [x for x in xs if x["kind"] == kind and x["sign"] == sign and x["legs"] == legs]
            print(kind, sign, legs, "n", len(g), "pos", sum(x["net"] > 0 for x in g), "median geo", np.median([x["geo"] for x in g]).round(2))
