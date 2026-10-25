"""DEV-only grid (2021-01-01..2024-10-31) for the short-term cross-sectional reversal book. Writes dev_grid.json.

Selection rule (fixed before this grid was run): among all grid rows, take the highest DEV arithmetic mean month at
baseline costs among rows whose DEV net is > 0 at doubled costs; if no row qualifies, take the highest DEV mean month at
baseline costs (then the TEST run is a formality and a clean FAIL is expected). Universe top-40, k = 10 per side, 1x.
"""
import json, sys, time
sys.path.insert(0, '.')
from data_r4 import build
from engine_r4 import HPanel, run
from scores import Scores
from common import DEV_START, DEV_END

KEYS = ("mean_month", "median_month", "geo_month", "max_dd_pct", "net_pct", "turnover_per_day", "cost_drag_month_pct", "liquidations")
t0 = time.time()
P = HPanel(build()); S = Scores(P)
rows = []
for kind in ("raw", "resid", "z", "zv"):
    for L in (1, 4, 12, 24, 72, 168):
        sc = S.get(kind, L)
        for R in (1, 4, 8, 24):
            for band in (1.0, 2.0):
                row = dict(kind=kind, L=L, R=R, band=band, k=10, univ=40)
                r = run(P, sc, 10, R, 40, DEV_START, DEV_END, band=band)
                row["base"] = {k: r[k] for k in KEYS}
                if band == 1.0:
                    g = run(P, sc, 10, R, 40, DEV_START, DEV_END, band=band, cost_mult=0.0)
                    row["gross"] = {k: g[k] for k in KEYS}
                rows.append(row)
                print(kind, L, R, band, row["base"]["mean_month"], row.get("gross", {}).get("mean_month"), f"[{time.time()-t0:.0f}s]", flush=True)
# doubled cost for the qualifying check
best = sorted(rows, key=lambda x: -x["base"]["mean_month"])
for row in best[:10]:
    r = run(P, S.get(row["kind"], row["L"]), 10, row["R"], 40, DEV_START, DEV_END, band=row["band"], cost_mult=2.0)
    row["double"] = {k: r[k] for k in KEYS}
qual = [x for x in best[:10] if x["double"]["net_pct"] > 0]
sel = qual[0] if qual else best[0]
json.dump({"rule": __doc__, "rows": rows, "selected": sel, "n_qualifying": len(qual)}, open("dev_grid.json", "w"), indent=1)
print("SELECTED", sel, "qualifying", len(qual))
