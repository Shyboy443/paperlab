"""DEV-only grid (2021-01..2024-10). Prints monthly stats per config. TEST untouched."""
import json, itertools, warnings
import numpy as np
warnings.filterwarnings("ignore")
from core import *
VR, Z, SG = features(0, 30, 480)
# DEV reference vol for equal-risk sizing: median sigma of coins with vr>=3 on DEV
m = (VR[:dT - d0] >= 3)
SIGREF = float(np.nanmedian(SG[:dT - d0][m]))
print("sig_ref DEV median", SIGREF)
res = []
for mode, thr, X, zmin in itertools.product(("mom", "rev", "long", "short"), (3, 5, 8), (1, 2, 3, 5), (0, 1, 2)):
    p = dict(hoff=0, Lb=30, W=480, thr=thr, zmin=zmin, X=X, K=5, mode=mode, sig_ref=SIGREF)
    c, tr, rows = sim(p, d0, dT)
    s = stats(c, tr)
    res.append(dict(p, med=s["median_month"], geo=s["geo_month"], mean=s["mean_month"], dd=s["max_dd_pct"], t=s["t_stat"], n=len(tr),
                    years=yearly(s["months"])))
    print(mode, thr, X, zmin, "med", s["median_month"], "geo", s["geo_month"], "mean", s["mean_month"], "dd", s["max_dd_pct"], "t", s["t_stat"], "n", len(tr), yearly(s["months"]), flush=True)
json.dump(dict(sig_ref=SIGREF, grid=res), open("dev_grid.json", "w"), indent=0)
