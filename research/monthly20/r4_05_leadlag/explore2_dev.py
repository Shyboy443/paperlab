"""DEV-only: split the event study by BTC direction and by year; laggards (catch-up) vs leaders (overshoot fade).
Gross % per event (equal-weight K names), entry next open."""
import sys, json
import numpy as np
from datetime import datetime, timezone
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DEV_START, DEV_END
from lib import logret, roll_beta, roll_std, lret
from data15 import load

tf = sys.argv[1] if len(sys.argv) > 1 else "1h"
P = load(tf)
t, syms, o, c, rank = P["t"], P["syms"], P["o"], P["c"], P["rank"]
bpd = 24 if tf == "1h" else 96
ib = list(syms).index("BTCUSDT")
r = logret(c); rb = r[:, ib]
beta = roll_beta(r, rb, 30 * bpd); sig1 = roll_std(rb, 30 * bpd)
dev = (t >= DEV_START) & (t < DEV_END)
o64 = o.astype(np.float64)
yr = np.array([datetime.fromtimestamp(x / 1000, timezone.utc).year for x in t[::bpd]]).repeat(bpd)[:len(t)]
grid = [(1, 3, 1), (1, 2, 1), (2, 3, 2), (4, 3, 2), (4, 3, 4), (4, 2, 4)] if tf == "1h" else \
       [(1, 3, 1), (1, 3, 4), (2, 3, 2), (4, 3, 4), (4, 3, 8), (8, 3, 8), (4, 2, 4)]
for L, z, H in grid:
    rbL = lret(c[:, ib], L); rL = lret(c, L); gap = beta * rbL[:, None] - rL
    idx = np.where(dev & (np.abs(rbL) > z * sig1 * np.sqrt(L)))[0]
    fwd = np.log(o64[idx + 1 + H] / o64[idx + 1]); s = np.sign(rbL[idx])[:, None]
    elig = (rank[idx] < 50) & ~np.isnan(gap[idx]) & ~np.isnan(fwd) & (np.arange(len(syms))[None, :] != ib)
    sg = np.where(elig, s * gap[idx], np.nan)
    rows = np.arange(len(idx))[:, None]
    lag = np.nanmean((s * fwd)[rows, np.argsort(-np.nan_to_num(sg, nan=-9), 1)[:, :5]], 1)
    lead = np.nanmean((s * fwd)[rows, np.argsort(np.nan_to_num(sg, nan=9), 1)[:, :5]], 1)
    allm = np.nanmean(np.where(elig, s * fwd, np.nan), 1)
    print(f"\nL={L} z={z} H={H} events={len(idx)}  (gross %, sign-adjusted: + = continuation of BTC's move)")
    for name, m in (("BTC up", s[:, 0] > 0), ("BTC down", s[:, 0] < 0)):
        print(f"  {name:8s} n={m.sum():4d} laggards {np.nanmean(lag[m])*100:+.3f}  leaders {np.nanmean(lead[m])*100:+.3f}  all {np.nanmean(allm[m])*100:+.3f}")
    for y in (2021, 2022, 2023, 2024):
        m = yr[idx] == y
        print(f"  {y} n={m.sum():4d} laggards {np.nanmean(lag[m])*100:+.3f}  leaders {np.nanmean(lead[m])*100:+.3f}  all {np.nanmean(allm[m])*100:+.3f}  "
              f"lag-lead {np.nanmean(lag[m]-lead[m])*100:+.3f}  median lead {np.nanmedian(lead[m])*100:+.3f}")
