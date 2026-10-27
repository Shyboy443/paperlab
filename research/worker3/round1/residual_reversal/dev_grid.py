"""DEV-only grid (2021-01..2024-10). Selection rule fixed before running:
eligible = feat in {rrev, rmom} AND DEV net > 0 at baseline AND doubled costs AND DEV median month > 0; pick highest DEV t.
raw_mom rows are info only (does the residual version beat plain momentum?), never selectable.
Beta window B = 60 days, dollar-neutral equal weight, universe top-40 are fixed (B varied only in check-5 neighbours)."""
import json, time
from core import *

t0 = time.time()
grid = []
for L in (1, 2, 3, 5, 7):
    for R in (1, 3, 7):
        for k in (5, 8):
            grid.append(dict(feat="rrev", L=L, skip=0, k=k, R=R, B=60))
for feat in ("rmom", "raw_mom"):
    for L in (14, 21, 30, 60):
        for s in (0, 3):
            for R in (3, 7):
                for k in (5, 8):
                    grid.append(dict(feat=feat, L=L, skip=s, k=k, R=R, B=60))
rows = []
for p in grid:
    c, _, n = sim(p, d0, dT)
    c2, _, _ = sim(p, d0, dT, cost_mult=2)
    s, s2 = stats(c, n), stats(c2)
    r = dict(p, median=s["median_month"], geo=s["geo_month"], mean=s["mean_month"], net=s["net_pct"], net_2x=s2["net_pct"],
             dd=s["max_dd_pct"], t=s["t_stat"], up=int(s["months_up"]), tpm=s["trades_per_month"])
    r = {a: (float(b) if hasattr(b, "dtype") else b) for a, b in r.items()}
    rows.append(r); print(r, flush=True)
el = [r for r in rows if r["feat"] in ("rrev", "rmom") and r["net"] > 0 and r["net_2x"] > 0 and r["median"] > 0]
sel = max(el, key=lambda r: r["t"]) if el else None
json.dump({"n_configs": len(rows), "n_eligible": len(el), "selected": sel, "rows": rows}, open("dev_grid.json", "w"), indent=1)
print("eligible", len(el), "selected", sel, "time", round(time.time() - t0))
