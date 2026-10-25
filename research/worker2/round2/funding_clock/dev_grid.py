"""DEV-only grid (2021-01..2024-10). TEST is not touched here."""
import itertools, json, sys
import numpy as np
from core import *

res = []
def ev(p, tag):
    c, tr, _ = sim(p, d0, dT)
    s = stats(c, tr)
    c2, tr2, _ = sim(p, d0, dT, cost_mult=2)
    s2 = stats(c2, tr2)
    yr = yearly(s["months"])
    r = dict(tag=tag, **{k: p[k] for k in p}, med=s["median_month"], geo=s["geo_month"], mean=s["mean_month"], dd=s["max_dd_pct"],
             t=s["t_stat"], tpm=s["trades_per_month"], net=s["net_pct"], net2x=s2["net_pct"], yrs=yr)
    res.append(r)
    print(json.dumps(r), flush=True)

mode = sys.argv[1] if len(sys.argv) > 1 else "legs"
if mode == "legs":
    for leg, which in itertools.product(("NEG", "POS"), ("pre", "post")):
        for thr in (0.0001, 0.0003, 0.0005, 0.001):
            for h in (1, 2, 3, 4, 6):
                p = dict(pre=which == "pre", h1=h if which == "pre" else 1, h2=h if which == "post" else 1,
                         thr_neg=thr, thr_pos=thr, K=5, NEG=which if leg == "NEG" else None, POS=which if leg == "POS" else None)
                ev(p, f"{leg}_{which}")
    json.dump(res, open("dev_grid_legs.json", "w"), indent=0)
if mode == "combo":
    for thr in (0.0005, 0.00075, 0.001, 0.0015):
        for h1 in (2, 3, 4):
            for h2 in (1, 2, 3):
                for K in (3, 5, 8):
                    for pos in (None, "post"):
                        p = dict(pre=True, h1=h1, h2=h2, thr_neg=thr, thr_pos=0.001, K=K, NEG="both", POS=pos)
                        ev(p, "NEGboth" + ("+POSpost" if pos else ""))
    json.dump(res, open("dev_grid_combo.json", "w"), indent=0)
