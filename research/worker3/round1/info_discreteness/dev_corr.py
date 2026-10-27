"""DEV-only: daily correlation of the top DEV candidates with Worker 1's book and with plain 14d rotation (LS10 weekly)."""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round1/info_discreteness")
from lib import run_cfg, daily_returns, P, DEV_START, DEV_END
W1 = np.load(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/_rbook_full_1x.npy")
d = json.load(open("dev_grid.json"))
ref = run_cfg("mom", 14, 10, "LS", 7, start=DEV_START, end=DEV_END, record=True)
_, rref = daily_returns(ref["_curve"])
out = []
for x in sorted([r for r in d["rows"] if r["kind"] != "mom"], key=lambda r: -r["t"])[:15]:
    r = run_cfg(x["kind"], x["L"], x["k"], x["legs"], x["rebal"], start=DEV_START, end=DEV_END, record=True)
    ts, dr = daily_returns(r["_curve"])
    i = ((ts - P.t[0]) // 86400000).astype(int)
    w = W1[i]; m = ~np.isnan(w)
    row = dict(cfg=f'{x["kind"]} L{x["L"]} k{x["k"]} {x["legs"]} rb{x["rebal"]}', t=x["t"], corr_w1=round(float(np.corrcoef(dr[m], w[m])[0, 1]), 3),
               corr_mom14=round(float(np.corrcoef(dr, rref)[0, 1]), 3))
    out.append(row); print(row, flush=True)
json.dump(out, open("dev_corr.json", "w"), indent=1)
