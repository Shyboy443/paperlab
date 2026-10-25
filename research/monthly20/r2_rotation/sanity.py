"""Sanity: (1) this engine on agent 02's own panel reproduces its posted P4 1x numbers; (2) survivor-only vs full archive."""
import sys, json
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/02_xs_momentum")
import engine_r2 as E
from data import build, OUT
from common import DEV_START, DEV_END, TEST_START, TEST_END

P4 = dict(k_long=10, k_short=10, rebal=7, lev=1.0, mode="mom")
res = {}
from dev_sweep import build as build02
P02, um02, _ = build02("1d")
sc = E.ret_lb(P02, 14)
for nm, a, b in (("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END)):
    r = E.run(P02, um02, sc, P4, a, b)
    res[f"agent02_panel_{nm}"] = {k: r[k] for k in ("mean_month", "median_month", "geo_month", "max_dd_pct", "net_pct")}
D = build()
for label, cols in (("archive_survivors_only", np.where(D["trading_today"])[0]), ("archive_all", None)):
    P = E.Panel(D, cols); um = E.universe_mask(P, 40); sc = E.ret_lb(P, 14)
    for nm, a, b in (("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END)):
        r = E.run(P, um, sc, P4, a, b)
        res[f"{label}_{nm}"] = {k: r[k] for k in ("mean_month", "median_month", "geo_month", "max_dd_pct", "net_pct")}
for k, v in res.items():
    print(k, v)
(OUT / "sanity.json").write_text(json.dumps(res, indent=1))
