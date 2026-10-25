"""DEV-only exploration grid (2021-01..2024-10). TEST is not touched here. Output: dev_grid.json"""
import json, sys, itertools
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_03_positioning")
import signals as S, engine as G
from common import DEV_START, DEV_END

P, UM, M = S.load()


def stats(r):
    v = np.array([x for x in r["months"].values()])
    act = v[v != 0]
    t = act.mean() / (act.std(ddof=1) / np.sqrt(len(act))) if len(act) > 2 else 0
    return dict(mean=r["mean_month"], geo=r["geo_month"], net=r["net_pct"], dd=r["max_dd_pct"], t_active=round(float(t), 2),
                n_active=int(len(act)), trades=r.get("trades"))


rows = []
for fam in ["squeeze", "retail_contra", "retail_chg", "smart_vs_retail", "taker_contra"]:
    for k in [1, 3, 7, 14]:
        sc = S.scores(P, M, fam, k)
        for (kL, kS), rb in itertools.product([(5, 5), (10, 10), (8, 0), (0, 8)], [1, 3, 7]):
            W, reb = S.xs_weights(P, UM, sc, kL, kS, rb, DEV_START)
            r = G.sim(P, W, DEV_START, DEV_END, rebal=reb)
            r2 = G.sim(P, W, DEV_START, DEV_END, rebal=reb, cost_mult=2)
            d = dict(fam=fam, k=k, kL=kL, kS=kS, rb=rb, **stats(r), net2=r2["net_pct"])
            rows.append(d); print(d, flush=True)
for k, drop, hold, slots in itertools.product([1, 3, 7], [0.1, 0.2, 0.3], [3, 7, 14], [5, 10]):
    W, _ = S.flush_weights(P, UM, M, k, drop, hold, slots, DEV_START)
    r = G.sim(P, W, DEV_START, DEV_END); r2 = G.sim(P, W, DEV_START, DEV_END, cost_mult=2)
    d = dict(fam="flush", k=k, drop=drop, hold=hold, slots=slots, **stats(r), net2=r2["net_pct"])
    rows.append(d); print(d, flush=True)
json.dump(rows, open(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_03_positioning/dev_grid.json", "w"), indent=0)
