"""Year-by-year view of the decay (rule P and the original r4_09), each year simulated from its own start (1x, 10k).
Writes decay.json."""
import json
from datetime import datetime, timezone
import numpy as np
from core import *

P = dict(SEL, sig_ref=0.01154)
out = {}
for nm, p in (("P", P), ("r4_09_original", SEL)):
    rows_out = {}
    for y in range(2021, 2027):
        a = dayidx(int(datetime(y, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
        b = min(d1, dayidx(int(datetime(y + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)))
        r = {}
        for cm in (1, 2):
            c, tr, _, _ = sim(p, a, b, cost_mult=cm)
            s = stats(c, tr)
            r["x%d" % cm] = {k: s[k] for k in ("net_pct", "median_month", "geo_month", "worst_month", "max_dd_pct", "months_up", "n_months")}
        c, tr, _, _ = sim(p, a, b)
        ret = np.array([t[6] for t in tr]); side = np.array([t[2] for t in tr])
        r["trades"] = len(tr)
        r["avg_trade_net_bps"] = round(float(ret.mean()) * 1e4, 1)
        r["win_rate"] = round(float((ret > 0).mean()) * 100, 1)
        r["long_avg_bps"] = round(float(ret[side > 0].mean()) * 1e4, 1)
        r["short_avg_bps"] = round(float(ret[side < 0].mean()) * 1e4, 1)
        r["share_long"] = round(float((side > 0).mean()), 2)
        rows_out[str(y)] = r
    out[nm] = rows_out
json.dump(out, open("decay.json", "w"), indent=1, default=float)
for nm, v in out.items():
    print(nm)
    for y, r in v.items():
        print(" ", y, r["x1"]["net_pct"], r["x1"]["median_month"], r["x1"]["geo_month"], r["x1"]["worst_month"], r["x1"]["max_dd_pct"],
              "| 2x", r["x2"]["net_pct"], "| n", r["trades"], "avg", r["avg_trade_net_bps"], "win", r["win_rate"], "L", r["long_avg_bps"], "S", r["short_avg_bps"], r["share_long"])
