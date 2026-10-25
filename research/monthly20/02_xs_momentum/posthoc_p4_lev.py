"""POST-HOC diagnostic (after TEST was seen, not pre-registered): P4 rules at other leverages, and P4 trade concentration."""
import json, sys
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/02_xs_momentum")
from engine import *
from signals import *
from dev_sweep import build
P, um, keep = build("1d")
sc = ret_lb(P, 14)
out = []
for lev in [1, 2, 3, 4, 5, 6]:
    p = dict(k_long=10, k_short=10, rebal=7, lev=lev, mode="mom")
    d = run(P, um, sc, p, DEV_START, DEV_END); t = run(P, um, sc, p, TEST_START, TEST_END)
    t2 = run(P, um, sc, p, TEST_START, TEST_END, cost_mult=2.0)
    row = dict(lev=lev, dev_mean=d["mean_month"], dev_geo=d["geo_month"], dev_dd=d["max_dd_pct"], dev_wiped=d["wiped"],
               test_mean=t["mean_month"], test_median=t["median_month"], test_geo=t["geo_month"], test_worst=t["worst_month"],
               test_dd=t["max_dd_pct"], test_wiped=t["wiped"], test_liq=t["liquidations"], test_mean_2x=t2["mean_month"])
    print(row, flush=True); out.append(row)
(MY_DATA / "posthoc_p4_lev.json").write_text(json.dumps(out, default=float))
