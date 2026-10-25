"""DEV-only screen (2021-01..2024-10). Writes dev_grid.json. Never touches TEST.

    python research/monthly20/r4_04_premium/dev.py
"""
import json, sys, time, itertools
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DEV_START, DEV_END, DAY, HOUR
import engine as E, signals as S
from panel import make


def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 2 and v.std() > 0 else 0.0


def load(step_h):
    D = make(step_h); P = E.Panel(D)
    D1 = make(24); P1 = E.Panel(D1)
    U1 = E.universe_daily(P1, 40)
    if step_h == 24:
        U = U1
    else:
        di = ((P.t - P1.t[0]) // DAY).astype(int)
        U = U1[di]
    U = U & ~np.isnan(P.o)
    return P, U


def configs():
    out = []
    # 1d panel: w, L in days
    for fam, w, L, k, rb, mode in itertools.product(["LEVEL", "Z", "MOM"], [1, 3, 7], [30, 60], [5, 10], [1, 3, 7], ["fade", "follow"]):
        if fam != "Z" and L != 30:
            continue
        out.append(dict(tf=24, kind="xs", fam=fam, w=w, L=L if fam == "Z" else None, k=k, rb=rb, mode=mode))
    for w, L, thr, rb, mode in itertools.product([1, 3], [30, 60], [1.5, 2.0, 2.5], [1, 3], ["fade", "follow"]):
        out.append(dict(tf=24, kind="ts", fam="Z", w=w, L=L, thr=thr, rb=rb, mode=mode))
    # 4h panel: w, L in 4h bars
    for fam, w, L, k, rb, mode in itertools.product(["LEVEL", "Z", "MOM"], [1, 3, 6], [42, 180], [5, 10], [1, 3], ["fade", "follow"]):
        if fam != "Z" and L != 42:
            continue
        out.append(dict(tf=4, kind="xs", fam=fam, w=w, L=L if fam == "Z" else None, k=k, rb=rb, mode=mode))
    for w, L, thr, rb, mode in itertools.product([1, 3], [42, 180], [2.0, 2.5, 3.0], [1, 3], ["fade", "follow"]):
        out.append(dict(tf=4, kind="ts", fam="Z", w=w, L=L, thr=thr, rb=rb, mode=mode))
    return out


def weights(P, U, c):
    sc = S.score(P, c["fam"], c["w"], c.get("L"))
    if c["kind"] == "xs":
        return S.xs_weights(sc, U, c["k"], c["mode"])
    return S.ts_weights(sc, U, c["thr"], c["mode"])


def name(c):
    return "_".join(f"{k}{v}" for k, v in c.items() if v is not None)


if __name__ == "__main__":
    res = []
    cache = {}
    t0 = time.time()
    for c in configs():
        if c["tf"] not in cache:
            cache[c["tf"]] = load(c["tf"])
        P, U = cache[c["tf"]]
        W = weights(P, U, c)
        r = E.run(P, W, c["rb"], DEV_START, DEV_END)
        r2 = E.run(P, W, c["rb"], DEV_START, DEV_END, cost_mult=2.0)
        row = dict(c, name=name(c), mean=r["mean_month"], median=r["median_month"], geo=r["geo_month"], dd=r["max_dd_pct"],
                   net=r["net_pct"], t=round(tstat(r["months"]), 2), trades=r.get("trades"), net2=r2["net_pct"], mean2=r2["mean_month"])
        res.append(row)
        print(row["name"], row["mean"], row["t"], row["net"], row["net2"], round(time.time() - t0), flush=True)
    (HERE / "dev_grid.json").write_text(json.dumps(res, indent=1))
