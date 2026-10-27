"""DEV-only neighbourhood scan of the leading DEV config (vchg L30 LS k5 monthly, top-100, TVL>=20M)."""
import json, sys
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round2/tvl_value")
import lib
from lib import run_cfg, tstat, DEV_START, DEV_END
base = dict(kind="vchg", L=30, k=5, legs="LS", rebal=30, univ=100, min_tvl=20e6)
G = [("L", v) for v in (15, 22, 38, 45)] + [("k", v) for v in (3, 4, 6, 8)] + [("rebal", v) for v in (7, 14, 21, 45)] + \
    [("univ", v) for v in (50, 75, 125, 150)] + [("min_tvl", v) for v in (10e6, 15e6, 25e6, 30e6)]
out = []
for name, v in [(None, None)] + G:
    kw = dict(base)
    if name: kw[name] = v
    r = run_cfg(kw["kind"], kw["L"], kw["k"], kw["legs"], kw["rebal"], univ=kw["univ"], min_tvl=kw["min_tvl"], start=DEV_START, end=DEV_END)
    r2 = run_cfg(kw["kind"], kw["L"], kw["k"], kw["legs"], kw["rebal"], univ=kw["univ"], min_tvl=kw["min_tvl"], start=DEV_START, end=DEV_END, cost_mult=2)
    out.append(dict(param=name, value=v, median=r["median_month"], geo=r["geo_month"], up=r["months_up"], dd=r["max_dd_pct"],
                    net=r["net_pct"], net2x=r2["net_pct"], t=round(tstat(r["months"]), 2), trades=r["trades"]))
    print(out[-1], flush=True)
json.dump(out, open(r"D:/Projects/BinanceBOt/paperlab/research/worker3/round2/tvl_value/dev_nbr.json", "w"), indent=0, default=float)
