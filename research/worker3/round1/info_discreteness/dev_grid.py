"""DEV-only grid (2021-01..2024-10). Never touches TEST. Writes dev_grid.json.
Selection rule (fixed before running): among quality kinds (fip, r2s, sharpe, resid), pick the config with the highest
DEV monthly t-stat, subject to DEV geo month > 0 and DEV net > 0 at doubled costs. 'mom' rows are reference only."""
import json, sys, time, itertools
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round1/info_discreteness")
from lib import run_cfg, tstat, DEV_START, DEV_END
from pathlib import Path

HERE = Path(__file__).resolve().parent
t0 = time.time()
rows = []
for kind, L, k, legs, rb in itertools.product(("mom", "fip", "r2s", "sharpe", "resid"), (14, 30, 60, 90), (5, 10),
                                              ("LS", "LO"), (1, 7)):
    r = run_cfg(kind, L, k, legs, rb, start=DEV_START, end=DEV_END)
    row = dict(kind=kind, L=L, k=k, legs=legs, rebal=rb, median=r["median_month"], geo=r["geo_month"], mean=r["mean_month"],
               up=r["months_up"], n=r["n_months"], dd=r["max_dd_pct"], net=r["net_pct"], t=round(tstat(r["months"]), 3),
               trades=r["trades"])
    rows.append(row)
    print(row, f"[{time.time()-t0:.0f}s]", flush=True)
cand = [x for x in rows if x["kind"] != "mom" and x["geo"] > 0]
for x in sorted(cand, key=lambda x: -x["t"])[:10]:
    r2 = run_cfg(x["kind"], x["L"], x["k"], x["legs"], x["rebal"], start=DEV_START, end=DEV_END, cost_mult=2)
    x["net_2xcost"] = r2["net_pct"]
ok = [x for x in cand if x.get("net_2xcost", -1) > 0]
sel = max(ok, key=lambda x: x["t"]) if ok else None
print("SELECTED", sel)
(HERE / "dev_grid.json").write_text(json.dumps(dict(rows=rows, selected=sel), indent=1, default=float))
