"""DEV-only grid (2021-01..2024-10). Never touches TEST. Writes dev_grid.json.
Selection rule (fixed before running):
  candidates = anchoring kinds (nh, rng) with DEV geo month > 0, DEV net > 0 at doubled costs,
               DEV daily corr with the Worker 1 book < 0.50 and with plain 14d LS10 weekly rotation < 0.70,
               DEV max DD <= 40%;  pick the highest DEV monthly t-stat.
  fallback (only if no candidate): drop the DD filter.  'mom' rows are reference only."""
import json, sys, time, itertools
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round2/high52")
from lib import run_cfg, tstat, corr_w1, daily_returns, DEV_START, DEV_END
from pathlib import Path

HERE = Path(__file__).resolve().parent
t0 = time.time()
ref = run_cfg("mom", 14, 10, "LS", 7, start=DEV_START, end=DEV_END, record=True)
_, rref = daily_returns(ref["_curve"])
LEGS = [("LS", 0), ("LO", 0), ("LO", 50)]
rows = []
combos = list(itertools.product(("nh", "rng"), (7, 14, 30, 60, 90, 180, 365), (5, 10), LEGS, (1, 7)))
combos += [("mom", L, k, lg, rb) for L in (14, 60) for k in (5, 10) for lg in LEGS for rb in (1, 7)]
for kind, L, k, (legs, gate), rb in combos:
    r = run_cfg(kind, L, k, legs, rb, gate=gate, start=DEV_START, end=DEV_END, record=True)
    _, dr = daily_returns(r["_curve"])
    row = dict(kind=kind, L=L, k=k, legs=legs, gate=gate, rebal=rb, median=r["median_month"], geo=r["geo_month"],
               mean=r["mean_month"], up=r["months_up"], n=r["n_months"], dd=r["max_dd_pct"], net=r["net_pct"],
               t=round(tstat(r["months"]), 3), trades=r["trades"], corr_w1=corr_w1(r["_curve"]),
               corr_mom14=round(float(np.corrcoef(dr, rref)[0, 1]), 3) if np.std(dr) > 0 else None)
    rows.append(row)
    print(row, f"[{time.time()-t0:.0f}s]", flush=True)


def pick(dd_filter):
    c = [x for x in rows if x["kind"] != "mom" and x["geo"] > 0 and x["corr_w1"] < 0.5
         and x["corr_mom14"] is not None and x["corr_mom14"] < 0.7 and (x["dd"] <= 40 or not dd_filter)]
    for x in sorted(c, key=lambda x: -x["t"])[:15]:
        if "net_2xcost" not in x:
            x["net_2xcost"] = run_cfg(x["kind"], x["L"], x["k"], x["legs"], x["rebal"], gate=x["gate"],
                                      start=DEV_START, end=DEV_END, cost_mult=2)["net_pct"]
    ok = [x for x in c if x.get("net_2xcost", -1) > 0]
    return max(ok, key=lambda x: x["t"]) if ok else None


sel = pick(True)
fallback = False
if sel is None:
    sel = pick(False); fallback = True
print("SELECTED", sel, "fallback" if fallback else "")
(HERE / "dev_grid.json").write_text(json.dumps(dict(rows=rows, selected=sel, fallback_used=fallback), indent=1, default=float))
