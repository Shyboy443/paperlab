"""Each leg alone through the ensemble engine at 1x vs the round-2 monthly numbers (full 2021-01..2026-10 only)."""
import json, sys, time
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble")
from ens import *
t0 = time.time()
D = Data()
print("panel", D.T, D.N, f"{time.time()-t0:.0f}s", flush=True)
R = leg_rotation(D); print("R", f"{time.time()-t0:.0f}s", flush=True)
M = leg_ml(D); print("M", f"{time.time()-t0:.0f}s", flush=True)
Tm = leg_mom(D); B = leg_btc(D)
legs = {"R": ("R", R, None), "M": ("M", M, trade_mask(D, M, "M")), "T": ("T", Tm, trade_mask(D, Tm, "T")), "B": ("B", B, trade_mask(D, B, "B"))}
M20 = r"D:/Projects/BinanceBOt/paperlab/research/monthly20/"
ref = {"R": json.load(open(M20 + "r2_rotation/results.json"))["monthly_1x"],
       "M": json.load(open(M20 + "r2_ml/results.json"))["monthly_1x"],
       "T": json.load(open(M20 + "r2_trend/results.json"))["T2"]["monthly_1x"]}
out = {}
for k, lg in legs.items():
    t1 = time.time()
    r = simulate(D, [lg], DEV_START, TEST_END)
    s = stats_of(r["curve"])
    row = {kk: s[kk] for kk in ("mean_month", "median_month", "geo_month", "max_dd_pct", "net_pct", "t_stat")}
    row["liq"] = r["liquidations"]
    if k in ref:
        a = np.array([s["months"][m] for m in ref[k]]); b = np.array(list(ref[k].values()))
        row["corr_vs_r2"] = round(float(np.corrcoef(a, b)[0, 1]), 3); row["r2_mean"] = round(float(b.mean()), 2)
    out[k] = row
    print(k, row, f"{time.time()-t1:.1f}s", flush=True)
json.dump(out, open("sanity.json", "w"), indent=1)
