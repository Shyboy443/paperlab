"""DEV-only grid (2021-01..2024-10). TEST is not touched here."""
import json, sys, time, itertools
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round2/tvl_value")
import numpy as np
import lib
from lib import run_cfg, tstat, DEV_START, DEV_END
t0 = time.time()
rows = []
for kind, L, (legs, gate), rebal, k in itertools.product(["tvlg", "vchg", "feeg", "revg", "fychg"], [14, 30, 60, 90],
                                                          [("LS", False), ("LO", True), ("LO", False)], [7, 30], [5]):
    r = run_cfg(kind, L, k, legs, rebal, gate=gate, start=DEV_START, end=DEV_END)
    rows.append(dict(kind=kind, L=L, legs=legs, gate=gate, rebal=rebal, k=k, median=r["median_month"], geo=r["geo_month"],
                     mean=r["mean_month"], up=r["months_up"], n=r["n_months"], dd=r["max_dd_pct"], net=r["net_pct"],
                     t=round(tstat(r["months"]), 2), trades=r["trades"]))
    print(rows[-1], f"[{time.time()-t0:.0f}s]", flush=True)
# reverse-sign (contrarian) LS versions too: a 'value' reading means the opposite sort for growth factors
for kind, L, rebal in itertools.product(["tvlg", "vchg", "feeg", "revg", "fychg"], [14, 30, 60, 90], [7, 30]):
    sc = -lib.score(kind, L)
    r = run_cfg(kind, L, 5, "LS", rebal, start=DEV_START, end=DEV_END, score_override=sc)
    rows.append(dict(kind="-" + kind, L=L, legs="LS", gate=False, rebal=rebal, k=5, median=r["median_month"], geo=r["geo_month"],
                     mean=r["mean_month"], up=r["months_up"], n=r["n_months"], dd=r["max_dd_pct"], net=r["net_pct"],
                     t=round(tstat(r["months"]), 2), trades=r["trades"]))
    print(rows[-1], flush=True)
json.dump(rows, open(r"D:/Projects/BinanceBOt/paperlab/research/worker3/round2/tvl_value/dev_grid.json", "w"), indent=0, default=float)
print("done", time.time() - t0)
