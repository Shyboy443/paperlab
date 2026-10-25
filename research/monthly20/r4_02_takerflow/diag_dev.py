"""DEV-only diagnostics for the daily cross-sectional taker-share signal: is it just momentum? which leg? which years?
Uses run_units (the real engine). Writes dev_diag.json."""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_02_takerflow")
import lib as Lb
from strat import build_W
from common import DEV_START, DEV_END, utc

P = Lb.panel("1d")
out = []


def rep(tag, W, **kw):
    r = Lb.run_units(P, W, DEV_START, DEV_END)
    yrs = {}
    for k, v in r["months"].items():
        yrs.setdefault(k[:4], []).append(v)
    y = {k: round((np.prod([1 + x / 100 for x in v]) - 1) * 100, 1) for k, v in yrs.items()}
    row = dict(tag=tag, **kw, mean=r["mean_month"], median=r["median_month"], geo=r["geo_month"], net=r["net_pct"],
               dd=r["max_dd_pct"], years=y, top3=r.get("top3_share_of_profit"))
    out.append(row); print(json.dumps(row), flush=True)


m = Lb.universe_mask(P, 40)
for L in (3, 7):
    for rb in (1, 3):
        base = dict(kind="XS", tf="1d", score="share", dir="follow", L=L, rb=rb)
        rep("share", build_W(P, base), L=L, rb=rb)
        rep("share_long10", build_W(P, dict(base, kS=0)), L=L, rb=rb)
        rep("share_short10", build_W(P, dict(base, k=0, kS=10)), L=L, rb=rb)
        # pure momentum with the same lookback, for comparison
        rep("momentum", Lb.xs_weights(Lb.ret_lb(P, L), m, 10, 10, rb), L=L, rb=rb)
        # taker share with momentum removed: cross-sectional residual of rank(share) on rank(ret)
        rs = Lb.xs_rank(Lb.taker_share(P, L), m); rr = Lb.xs_rank(Lb.ret_lb(P, L), m)
        res = np.full(rs.shape, np.nan)
        for i in range(len(rs)):
            ok = np.isfinite(rs[i]) & np.isfinite(rr[i])
            if ok.sum() > 5:
                b = np.polyfit(rr[i, ok], rs[i, ok], 1)
                res[i, ok] = rs[i, ok] - np.polyval(b, rr[i, ok])
        rep("share_resid_mom", Lb.xs_weights(res, m, 10, 10, rb), L=L, rb=rb)
        # correlation of share rank with ret rank
        cc = [np.corrcoef(rs[i, np.isfinite(rs[i]) & np.isfinite(rr[i])], rr[i, np.isfinite(rs[i]) & np.isfinite(rr[i])])[0, 1]
              for i in range(len(rs)) if (np.isfinite(rs[i]) & np.isfinite(rr[i])).sum() > 5]
        print("corr rank(share) vs rank(ret)", L, np.nanmean(cc), flush=True)
    for U in (20, 60, 100):
        rep("share_U", build_W(P, dict(kind="XS", tf="1d", score="share", dir="follow", L=L, rb=3, U=U)), L=L, rb=3, U=U)
json.dump(out, open("dev_diag.json", "w"), indent=0)
