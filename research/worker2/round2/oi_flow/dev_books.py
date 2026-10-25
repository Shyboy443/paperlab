"""DEV only. Step 2b: a fixed set of regime->sign maps (declared before running), each over the full k x q x h grid,
simulated on DEV (2021-12..2024-10 effectively) with baseline and doubled costs. Writes dev_books.json."""
import json, itertools
import numpy as np
import core as C

SIGNMAPS = {
    "hypothesis":   {"UU": -1, "DU": +1, "UD": -1, "DD": +1},   # crowded new longs/short covering faded, squeeze fuel + flush bought
    "anti_hyp":     {"UU": +1, "DU": -1, "UD": +1, "DD": -1},
    "shortDU_longUU": {"UU": +1, "DU": -1},                        # OI up: follow price (new money is right)
    "shortDU_longDD": {"DD": +1, "DU": -1},                        # price down: OI up bad, OI down (flush) ok
    "shortDU_longUD": {"UD": +1, "DU": -1},
}
res = []
for name, sg in SIGNMAPS.items():
    for k, q, h in itertools.product((1, 2, 3, 5, 7), (0.2, 0.3), (1, 2, 3)):
        W = C.weights(k, q, h, sg)
        d = C.run(W, C.DEV_START, C.DEV_END); d2 = C.run(W, C.DEV_START, C.DEV_END, cost_mult=2)
        v = np.array([x for m, x in d["months"].items() if m >= "2021-12"])
        t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
        b = dict(map=name, k=k, q=q, h=h, median=d["median_month"], geo=d["geo_month"], mean=d["mean_month"],
                 net=d["net_pct"], dd=d["max_dd_pct"], t=round(float(t), 2), net2=d2["net_pct"], trades=d["trades"])
        res.append(b); print(b, flush=True)
json.dump(res, open("dev_books.json", "w"), indent=1, default=float)
