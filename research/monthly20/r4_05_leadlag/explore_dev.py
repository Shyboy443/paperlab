"""DEV-only event study (2021-01..2024-10): does a sharp BTC move predict catch-up of lagging alts?
Gross numbers per trade, no compounding. Entry at the OPEN of the bar after the signal bar, exit at an open H bars later.
Usage: python explore_dev.py 1h | 15m
"""
import sys, json
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DEV_START, DEV_END
from lib import logret, roll_beta, roll_std, lret
from data15 import load

tf = sys.argv[1] if len(sys.argv) > 1 else "1h"
P = load(tf)
t, syms, o, c, rank = P["t"], P["syms"], P["o"], P["c"], P["rank"]
bpd = 24 if tf == "1h" else 96
ib = list(syms).index("BTCUSDT")
r = logret(c)
rb = r[:, ib]
beta = roll_beta(r, rb, 30 * bpd)
sig1 = roll_std(rb, 30 * bpd)
dev = (t >= DEV_START) & (t < DEV_END)
o64 = o.astype(np.float64)
res = []
for L in ([1, 2, 4] if tf == "1h" else [1, 2, 4, 8]):
    rbL = lret(c[:, ib], L)
    rL = lret(c, L)
    gap = beta * rbL[:, None] - rL
    for z in (2.0, 3.0):
        trig = dev & (np.abs(rbL) > z * sig1 * np.sqrt(L))
        idx = np.where(trig)[0]
        idx = idx[idx + 30 < len(t)]
        for H in ([1, 2, 4, 8] if tf == "1h" else [1, 2, 4, 8, 16]):
            fwd = np.log(o64[idx + 1 + H] / o64[idx + 1])
            fb = fwd[:, ib]
            for N in (20, 50):
                s = np.sign(rbL[idx])[:, None]
                elig = (rank[idx] < N) & ~np.isnan(gap[idx]) & ~np.isnan(fwd) & (np.arange(len(syms))[None, :] != ib)
                sg = np.where(elig, s * gap[idx], np.nan)
                dirr = s * fwd
                hed = s * (fwd - beta[idx] * fb[:, None])
                # all eligible alts, and top-5 laggards per event
                allm = np.nanmean(np.where(elig, dirr, np.nan))
                k = 5
                top = np.argsort(-np.nan_to_num(sg, nan=-9), 1)[:, :k]
                rows = np.arange(len(idx))[:, None]
                tk = np.nanmean(dirr[rows, top], 1); th = np.nanmean(hed[rows, top], 1)
                bot = np.argsort(np.nan_to_num(sg, nan=9), 1)[:, :k]
                bk = np.nanmean(dirr[rows, bot], 1)
                # rank IC of gap vs hedged forward return
                res.append(dict(L=L, z=z, H=H, N=N, events=len(idx), btc_cont=round(float(np.mean(s[:, 0] * fb)) * 100, 3),
                                all_alts=round(float(allm) * 100, 3), top5_dir=round(float(np.nanmean(tk)) * 100, 3),
                                top5_hedged=round(float(np.nanmean(th)) * 100, 3), leaders5_dir=round(float(np.nanmean(bk)) * 100, 3)))
                print(res[-1], flush=True)
json.dump(res, open(f"dev_event_{tf}.json", "w"), indent=0)
