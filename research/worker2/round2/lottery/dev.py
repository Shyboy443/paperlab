"""DEV-only grid (2021-01..2024-10). Selection rule fixed before running:
eligible = DEV net > 0 at baseline AND doubled costs AND DEV median month > 0; pick the highest DEV t-stat.
Rebalance R = 7 days and equal-risk sizing are fixed in the grid (R varied only in the check-5 neighbours)."""
import json, time
import numpy as np
from core import *

t0 = time.time()
rows = []
for feat in ("max1d", "max5d", "maxh", "skewh", "skewd", "smax", "smaxh"):
    for L in (7, 14, 30):
        if feat in ("max5d", "smax", "skewd") and L < 14:
            continue
        for k in (5, 8):
            p = dict(feat=feat, L=L, k=k, R=7, sizing="erisk")
            c, _, n = sim(p, d0, dT)
            c2, _, _ = sim(p, d0, dT, cost_mult=2)
            s, s2 = stats(c, n), stats(c2)
            r = dict(p, median=s["median_month"], geo=s["geo_month"], mean=s["mean_month"], net=s["net_pct"],
                     net_2x=s2["net_pct"], dd=s["max_dd_pct"], t=s["t_stat"], tpm=s["trades_per_month"])
            rows.append(r); print(r, round(time.time() - t0), flush=True)
el = [r for r in rows if r["net"] > 0 and r["net_2x"] > 0 and r["median"] > 0]
sel = max(el, key=lambda r: r["t"]) if el else None
json.dump({"n_configs": len(rows), "n_eligible": len(el), "selected": sel, "rows": rows}, open("dev_grid.json", "w"), indent=1)
print("eligible", len(el), "selected", sel)
