"""DEV-only exploration of the taker-flow family (2021-01..2024-10). Every variant tried is written to dev_explore.json.
Fast screen (lib.fast_eval) at 1x, baseline costs. TEST is not touched here."""
import json, sys, time, itertools
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_02_takerflow")
import lib as Lb
from common import DEV_START, DEV_END

t0 = time.time()
rows = []


def rec(tag, P, W, **kw):
    s = Lb.fast_eval(P, W, DEV_START, DEV_END)
    s2 = Lb.fast_eval(P, W, DEV_START, DEV_END, cost_mult=2)
    r = dict(tag=tag, tf=P["tf"], **kw, mean=s["mean_month"], median=s["median_month"], geo=s["geo_month"],
             net=s["net_pct"], dd=s["max_dd_pct"], net2x=s2["net_pct"], turn=round(s["turnover_per_year"], 1))
    rows.append(r)
    print(json.dumps(r), f"[{time.time()-t0:.0f}s]", flush=True)


U = 40
SPEC = {  # tf: (lookbacks in bars, rebalance periods in bars)
    "1d": ([1, 3, 7, 14], [1, 3, 7]),
    "4h": ([1, 3, 6, 18], [1, 6]),
    "1h": ([4, 12, 24], [4, 24]),
}
for tf, (LBS, RBS) in SPEC.items():
    P = Lb.panel(tf)
    m = Lb.universe_mask(P, U)
    for L in LBS:
        scores = {
            "share": Lb.taker_share(P, L),
            "fdev": Lb.flow_dev(P, L),
            "fvol": Lb.flow_vol(P, L),
        }
        rk_f = Lb.xs_rank(scores["fdev"], m)
        rk_r = Lb.xs_rank(Lb.ret_lb(P, L), m)
        scores["div"] = rk_f - rk_r        # high = buying flow but price lagging ("accumulation")
        for name, sc in scores.items():
            for rb in RBS:
                for sgn, lab in ((1, "follow"), (-1, "fade")):
                    W = Lb.xs_weights(sgn * sc, m, 10, 10, rb)
                    rec("XS_LS10", P, W, score=name, dir=lab, L=L, rb=rb)
        # time-series per coin
        fd = scores["fdev"]; rt = Lb.ret_lb(P, L)
        sd = np.nanstd(fd[: len(fd) // 2])
        for thr_k in (0.5, 1.0):
            thr = thr_k * sd
            sig_flow = np.where(fd > thr, 1.0, np.where(fd < -thr, -1.0, 0.0))
            sig_div = np.where((rt < 0) & (fd > thr), 1.0, np.where((rt > 0) & (fd < -thr), -1.0, 0.0))
            sig_conf = np.where((rt > 0) & (fd > thr), 1.0, np.where((rt < 0) & (fd < -thr), -1.0, 0.0))
            for nm, sg in (("ts_flow", sig_flow), ("ts_div", sig_div), ("ts_conf", sig_conf)):
                for rb in RBS:
                    for sgn, lab in ((1, "follow"), (-1, "fade")):
                        W = Lb.ts_weights(sgn * sg, m, U, rb)
                        rec("TS", P, W, score=nm, dir=lab, L=L, rb=rb, thr_sd=thr_k)
                        Wl = np.where(np.nan_to_num(W) > 0, W, np.where(np.isnan(W), np.nan, 0.0))
                        rec("TS_long", P, Wl, score=nm, dir=lab, L=L, rb=rb, thr_sd=thr_k)
    Lb._CACHE.pop(tf, None)
    json.dump(rows, open("dev_explore.json", "w"), indent=0)
json.dump(rows, open("dev_explore.json", "w"), indent=0)
print("done", len(rows), f"[{time.time()-t0:.0f}s]")
