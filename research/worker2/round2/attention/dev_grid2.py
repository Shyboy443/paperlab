"""DEV-only: K (concentration) and exposure for the continuation (mom) family."""
import json, itertools, warnings
import numpy as np
warnings.filterwarnings("ignore")
from core import *
SIGREF = json.load(open("dev_grid.json"))["sig_ref"]
res = []
for thr, zmin, X, K in itertools.product((3, 5), (1, 1.5, 2), (2, 3, 4), (1, 2, 3, 5)):
    p = dict(hoff=0, Lb=30, W=480, thr=thr, zmin=zmin, X=X, K=K, mode="mom", sig_ref=SIGREF)
    c, tr, rows = sim(p, d0, dT)
    s = stats(c, tr)
    gross = sum(t[5] * X for t in tr) / (dT - d0)  # avg gross exposure (notional/eq summed over days held / days)
    pc = {}
    for t in tr: pc[t[1]] = pc.get(t[1], 0) + t[3]
    tot = c[-1][1] - c[0][1]
    mx = max(t[3] for t in tr) / tot if tot > 0 else None
    nl = sum(t[2] > 0 for t in tr)
    yr = yearly(s["months"])
    r = dict(thr=thr, zmin=zmin, X=X, K=K, med=s["median_month"], geo=s["geo_month"], mean=s["mean_month"], dd=s["max_dd_pct"], t=s["t_stat"],
             n=len(tr), longs=nl, gross=round(gross, 3), maxpos_share=round(mx, 3) if mx else None, years=yr)
    res.append(r); print(r, flush=True)
json.dump(res, open("dev_grid2.json", "w"), indent=0)
