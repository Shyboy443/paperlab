"""DEV only (2021-12..2024-10; metrics start 2021-12-01). Step 1: event study of the 4 regimes' forward
cross-sectional return. Step 2: the long/short book for each (k, q, h) with signs = sign of the DEV regime mean
(0 if |t| < 1.5), simulated on DEV with costs. Writes dev_scan.json. TEST is not touched."""
import json, itertools
import numpy as np
from datetime import datetime, timezone
import core as C

i_dev0 = int(np.searchsorted(C.P.t, C.DEV_START)); i_dev1 = int(np.searchsorted(C.P.t, C.DEV_END))
out = {"event": [], "book": []}
KS, QS, HS = (1, 2, 3, 5, 7), (0.2, 0.3), (1, 2, 3)
F = {h: C.fwd_xs(h) for h in HS}
for k, q in itertools.product(KS, QS):
    R, ok = C.regime_masks(k, q)
    for h in HS:
        rows = slice(i_dev0, i_dev1 - h - 2)
        f = F[h][rows]
        e = {"k": k, "q": q, "h": h}
        for r in C.REGIMES:
            m = R[r][rows] & ~np.isnan(f)
            # daily means then t over days (cross-sectional dependence)
            dm = np.array([f[d][m[d]].mean() for d in range(m.shape[0]) if m[d].sum() > 0])
            # non-overlapping t: scale by sqrt(h)
            t = dm.mean() / (dm.std(ddof=1) / np.sqrt(len(dm))) / np.sqrt(h)
            e[r] = (round(float(dm.mean()) * 100, 1), round(float(t), 2))  # % of one h-day sigma
        out["event"].append(e)
        print("event", e, flush=True)

for e in out["event"]:
    signs = {r: (int(np.sign(e[r][0])) if abs(e[r][1]) >= 1.5 else 0) for r in C.REGIMES}
    if not any(v > 0 for v in signs.values()) or not any(v < 0 for v in signs.values()):
        out["book"].append(dict(k=e["k"], q=e["q"], h=e["h"], signs=signs, skipped="no long or no short regime"))
        continue
    W = C.weights(e["k"], e["q"], e["h"], signs)
    d = C.run(W, C.DEV_START, C.DEV_END); d2 = C.run(W, C.DEV_START, C.DEV_END, cost_mult=2)
    v = np.array([x for m, x in d["months"].items() if m >= "2021-12"])
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    b = dict(k=e["k"], q=e["q"], h=e["h"], signs=signs, median=d["median_month"], geo=d["geo_month"], mean=d["mean_month"],
             net=d["net_pct"], dd=d["max_dd_pct"], t=round(float(t), 2), net2=d2["net_pct"], trades=d["trades"])
    out["book"].append(b); print("book", b, flush=True)
json.dump(out, open("dev_scan.json", "w"), indent=1, default=float)
