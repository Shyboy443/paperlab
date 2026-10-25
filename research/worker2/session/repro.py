"""Step 1: reproduce r4_09's selected numbers with core.py (m=1 for every row). Also computes sig_ref from DEV signal rows only."""
import json, numpy as np
from core import *
out = {}
ref = json.load(open(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_09_session/results.json"))["attempts"][0]
keys = ("mean_month", "median_month", "geo_month", "worst_month", "max_dd_pct", "net_pct", "trades", "t_stat")
for tag, (a, b) in (("dev", (d0, dT)), ("test", (dT, d1)), ("full", (d0, d1))):
    c, tr, _, _ = sim(SEL, a, b); s = stats(c, tr)
    out[tag] = {k: s[k] for k in keys}
    out[tag + "_r4_results_json"] = {k: ref[tag][k] for k in keys}
    out[tag + "_match"] = all(abs(float(s[k]) - float(ref[tag][k])) < 1e-6 for k in keys)
    print(tag, out[tag], out[tag + "_match"])
# check-7 numbers
c, tr, _, _ = sim(SEL, d0, d1)
total = c[-1][1] - c[0][1]
i = int(np.argmax([t[3] for t in tr]))
out["max_position_share_of_profit"] = round(tr[i][3] / total, 4)
out["max_position_pct_of_equity"] = round(max(t[4] for t in tr) * 100, 2)
print("check7", out["max_position_share_of_profit"], out["max_position_pct_of_equity"])
# sig_ref: DEV-only statistic of the sizing input (no returns used)
V = hv(480)
sig = np.array([V[r[0] - d0, r[3]] for r in signals(SEL, d0, dT)])
sig = sig[np.isfinite(sig)]
out["dev_signal_sigma_pct"] = {q: round(float(np.percentile(sig, q)) * 100, 4) for q in (10, 25, 50, 75, 90)}
print(out["dev_signal_sigma_pct"])
json.dump(out, open("repro.json", "w"), indent=1, default=float)
