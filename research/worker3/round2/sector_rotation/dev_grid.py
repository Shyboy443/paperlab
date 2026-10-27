"""DEV-only grid (2021-01..2024-10). Never touches TEST."""
import json, sys, itertools
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core
from core import run_cfg, tstat, DEV_START, DEV_END

HERE = Path(__file__).resolve().parent
rows = []
for K, S, mode, univ in itertools.product((14, 30, 60, 90), (1, 2), ("LS", "LO", "LOG"), (40, 80, 120)):
    r = run_cfg(K, S, mode, univ, 7, DEV_START, DEV_END)
    r2 = run_cfg(K, S, mode, univ, 7, DEV_START, DEV_END, cost_mult=2)
    nm = r["n_months"]
    row = dict(K=K, S=S, mode=mode, univ=univ, median=r["median_month"], geo=r["geo_month"], mean=r["mean_month"],
               up=r["months_up"], n=nm, dd=r["max_dd_pct"], t=round(tstat(r["months"]), 2), net=r["net_pct"],
               net2x=r2["net_pct"], orders_mo=round(r["orders"] / nm, 1))
    rows.append(row)
    print(row, flush=True)
(HERE / "dev_grid.json").write_text(json.dumps(rows, indent=1, default=float))
