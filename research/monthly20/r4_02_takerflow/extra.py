"""Context numbers for the report (no re-tuning): biggest positions, TEST without its best 3 months, legs on TEST."""
import json, sys
import numpy as np
from datetime import datetime, timezone
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_02_takerflow")
import lib as Lb
from strat import build_W
from common import DEV_START, TEST_START, TEST_END

R = json.load(open("results.json")); CFG = R["selected"]
P = Lb.panel("1d")
full = Lb.run_units(P, build_W(P, CFG), DEV_START, TEST_END, record=True)
eq = dict(full["_curve"])
pos = sorted(full["_positions"], key=lambda p: -p[4])
tot = sum(p[4] for p in full["_positions"])
d = lambda i: datetime.fromtimestamp(P["t"][i] / 1000, timezone.utc).strftime("%Y-%m-%d")
top = [dict(coin=str(P["syms"][j]), side=s, opened=d(o), closed=d(min(c, len(P["t"]) - 1)), pnl=round(p, 0),
            share_of_total_pct=round(p / tot * 100, 2)) for j, s, o, c, p in pos[:8]]
tm = R["test_months_1x"]; b3 = sorted(tm, key=tm.get, reverse=True)[:3]
ex = (np.prod([1 + v / 100 for k, v in tm.items() if k not in b3]) - 1) * 100
legs = {}
for lab, ch in (("long_only", dict(kS=0)), ("short_only", dict(k=0, kS=10))):
    r = Lb.run_units(P, build_W(P, dict(CFG, **ch)), TEST_START, TEST_END)
    legs[lab] = dict(mean=r["mean_month"], median=r["median_month"], geo=r["geo_month"], net=r["net_pct"], dd=r["max_dd_pct"])
R["extra"] = dict(top_positions=top, test_without_best3_months=dict(best3=b3, net=round(ex, 2)), test_legs_info_only=legs)
json.dump(R, open("results.json", "w"), indent=1, default=float)
print(json.dumps(R["extra"], indent=1, default=float))
