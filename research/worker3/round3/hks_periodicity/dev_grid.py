"""DEV-only grid (2021-01..2024-10). Gross (cost 0) and baseline-cost results for every config; all traded blocks."""
import json, itertools, time, warnings
warnings.filterwarnings("ignore")
import lib
out = []
t0 = time.time()
for B, K, k in itertools.product((1, 2, 3, 4, 6, 8, 12), (5, 10, 20, 40), (3, 5, 10)):
    g = lib.run(B=B, K=K, k=k, start=lib.DEV_START, end=lib.DEV_END, cost_mult=0)
    n = lib.run(B=B, K=K, k=k, start=lib.DEV_START, end=lib.DEV_END, cost_mult=1)
    row = dict(B=B, K=K, k=k, gross_geo=float(g["geo_month"]), net_geo=float(n["geo_month"]), net_median=float(n["median_month"]),
               net_up=int(n["months_up"]), net_dd=float(n["max_dd_pct"]), trades_mo=round(n["trades"] / n["n_months"]))
    out.append(row); print(row, f"[{time.time()-t0:.0f}s]", flush=True)
json.dump(out, open("dev_grid.json", "w"), indent=1)
