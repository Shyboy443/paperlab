"""DEV-only exploration: hourly drift around funding stamps conditional on the last known funding rate."""
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_09_session")
from base import *
import hpanel
hf = hpanel.make()["has_f"]
d0, dT = dayidx(DEV_START), dayidx(TEST_START)
print("coins", len(syms), "has_f", hf.sum())
FC = FCUM
def F8(t, j):  # funding stamps in bars t-8..t-1 (8h-equivalent last known rate) known at open of t
    return FC[t, j] - FC[t - 8, j]
rows = []
for d in range(d0, dT):
    js = np.where(U40[d] & hf)[0]
    for hh in (0, 8, 16):
        s = d * 24 + hh
        f_pre = FC[s - 3, js] - FC[s - 11, js]   # known 3h before stamp (prev stamp at s-8)
        f_now = FC[s + 1, js] - FC[s - 7, js]    # includes stamp at s (known at s)
        lr = LR[s - 6:s + 8, js]                # bars s-6..s+7
        rows.append((np.full(len(js), s), js, f_pre, f_now, lr.T))
S = np.concatenate([r[0] for r in rows]); Jj = np.concatenate([r[1] for r in rows])
FP = np.concatenate([r[2] for r in rows]); FN = np.concatenate([r[3] for r in rows]); LRm = np.concatenate([r[4] for r in rows])
print("obs", len(S))
print("funding pct (8h eq):", np.percentile(FP, [1, 5, 25, 50, 75, 95, 99]) * 100)
hdr = "bucket       n    " + " ".join(f"{h:+d}".rjust(6) for h in range(-6, 8))
for name, F in (("f_pre", FP), ("f_now", FN)):
    print("\nmean hourly return (bp) by", name, "; column h = bar starting s+h (stamp at s)")
    print(hdr)
    for lo, hi in ((-1, -0.0005), (-0.0005, -0.0001), (-0.0001, 0.00005), (0.00005, 0.000101), (0.000101, 0.0003), (0.0003, 0.0007), (0.0007, 1)):
        m = (F > lo) & (F <= hi)
        mu = np.nanmean(LRm[m], 0) * 1e4
        print(f"({lo*100:+.3f},{hi*100:+.3f}] {m.sum():6d} " + " ".join(f"{x:6.1f}" for x in mu))
# by year for extreme buckets
yr = np.array([int(1970 + (td[s // 24] / 1000) / 31556952) for s in S])
print("\nper-year sums (bp) pre window s-3..s and post window s..s+3, by extreme buckets")
for nm, m in (("f>=0.03%", FP >= 0.0003), ("f<=-0.03%", FP <= -0.0003), ("f>=0.07%", FP >= 0.0007), ("f<=-0.05%", FP <= -0.0005)):
    for y in range(2021, 2025):
        k = m & (yr == y)
        pre = np.nansum(LRm[k][:, 3:6], 1); post = np.nansum(LRm[k][:, 6:9], 1)
        print(nm, y, k.sum(), "pre3 %.1f post3 %.1f" % (np.nanmean(pre) * 1e4, np.nanmean(post) * 1e4))
