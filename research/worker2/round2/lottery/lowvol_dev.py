"""DEV only: how close is the MAX sort to plain low-vol? (rank corr of scores; corr of the two books' monthly returns)"""
import json, numpy as np
from scipy.stats import spearmanr
from core import *
out = {}
for feat, L in (("max1d", 14), ("smax", 14), ("skewh", 14), ("maxh", 14)):
    rc, ov = [], []
    for d in range(d0, dT, 7):
        s = feature(feat, L, d); v = feature("vol", 14, d)
        m = U40[d] & np.isfinite(s) & np.isfinite(v)
        if m.sum() > 10:
            rc.append(spearmanr(s[m], v[m])[0])
            idx = np.where(m)[0]
            a = set(idx[np.argsort(s[idx])][-8:]); b = set(idx[np.argsort(v[idx])][-8:])
            ov.append(len(a & b) / 8)
    out[feat] = {"mean_rank_corr_with_vol": round(float(np.mean(rc)), 3), "short_leg_overlap_with_highvol_top8": round(float(np.mean(ov)), 3)}
p = dict(feat="max1d", L=14, k=8, R=7, sizing="erisk"); q = dict(p, feat="vol")
a = stats(sim(p, d0, dT)[0]); b = stats(sim(q, d0, dT)[0])
ks = sorted(set(a["months"]) & set(b["months"]))
out["book_monthly_corr_max1d_vs_lowvol_DEV"] = round(float(np.corrcoef([a["months"][k] for k in ks], [b["months"][k] for k in ks])[0, 1]), 3)
out["lowvol_book_DEV"] = {k: b[k] for k in ("median_month", "geo_month", "mean_month", "net_pct", "t_stat")}
json.dump(out, open("lowvol_dev.json", "w"), indent=1); print(out)
