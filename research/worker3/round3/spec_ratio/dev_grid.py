"""DEV-only grid (2021-01..2024-10). Never touches TEST. Writes dev_grid.json.
Grid: kind {lvl, chg} x L {7, 14, 30} x sign {+1, -1} x k {5, 10} x rebal 7 (weekly) x univ top-40, LS 1x  = 24 rows,
plus univ 60 for the same 24 (48 rows total).
Selection rule (fixed before running):
  1. sign decided on DEV: the sign whose rows have the higher median DEV t-stat (all kinds/L/k/univ pooled);
  2. within that sign: DEV geo > 0, DEV net > 0 at doubled costs, DEV max DD <= 40%, DEV daily corr with Worker 1 < 0.5;
     pick the highest DEV monthly t-stat.  Fallback without the DD filter if none qualifies."""
import json, sys, time, itertools
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round3/spec_ratio")
import lib
from lib import run_cfg, tstat, corr_w1, DEV_START, DEV_END
from pathlib import Path

HERE = Path(__file__).resolve().parent
t0 = time.time()
print("spot sanity rejects:", lib.sanity()[:50], flush=True)
rows = []
for kind, L, sign, k, univ in itertools.product(("lvl", "chg"), (7, 14, 30), (1, -1), (5, 10), (40, 60)):
    r = run_cfg(kind, L, sign, k, 7, univ=univ, start=DEV_START, end=DEV_END, record=True)
    row = dict(kind=kind, L=L, sign=sign, k=k, rebal=7, univ=univ, median=r["median_month"], geo=r["geo_month"],
               mean=r["mean_month"], up=r["months_up"], n=r["n_months"], dd=r["max_dd_pct"], net=r["net_pct"],
               t=round(tstat(r["months"]), 3), trades=r["trades"], corr_w1=corr_w1(r["_curve"]))
    rows.append(row)
    print(row, f"[{time.time()-t0:.0f}s]", flush=True)
med_t = {s: float(np.median([x["t"] for x in rows if x["sign"] == s])) for s in (1, -1)}
SIGN = max(med_t, key=med_t.get)


def pick(dd_filter):
    c = [x for x in rows if x["sign"] == SIGN and x["geo"] > 0 and x["corr_w1"] < 0.5 and (x["dd"] <= 40 or not dd_filter)]
    for x in c:
        x["net_2xcost"] = run_cfg(x["kind"], x["L"], x["sign"], x["k"], 7, univ=x["univ"], start=DEV_START, end=DEV_END, cost_mult=2)["net_pct"]
    ok = [x for x in c if x["net_2xcost"] > 0]
    return max(ok, key=lambda x: x["t"]) if ok else None


sel = pick(True); fb = False
if sel is None:
    sel = pick(False); fb = True
print("median t by sign", med_t, "SIGN", SIGN)
print("SELECTED", sel, "fallback" if fb else "")
(HERE / "dev_grid.json").write_text(json.dumps(dict(rows=rows, median_t_by_sign=med_t, sign=SIGN, selected=sel, fallback_used=fb), indent=1, default=float))
